"""
Bilingual (Persian + English) Security NLP/Regex Free-Text Classifier & 15-Category Threat Taxonomy.
Supports:
- Built-in dictionary & regex status/category extraction with explicit extraction_reason
- Admin-defined SecurityClassificationRule precedence (status_map, category_map)
- Software product + version + CVE token extraction
- Dataset-level free-text column security classification
"""
from __future__ import annotations

import re
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.intelligence import SecurityClassificationRule

THREAT_CATEGORIES: list[dict[str, str]] = [
    {"key": "malware_ransomware", "label_fa": "بدافزار و باج‌افزار", "default_severity": "critical"},
    {"key": "phishing_social", "label_fa": "فیشینگ و مهندسی اجتماعی", "default_severity": "high"},
    {"key": "brute_force_auth", "label_fa": "حمله ورود و رمزگشایی (Brute Force)", "default_severity": "high"},
    {"key": "privilege_escalation", "label_fa": "ارتقای غیرمجاز سطح دسترسی", "default_severity": "critical"},
    {"key": "lateral_movement", "label_fa": "حرکت جانبی در شبکه", "default_severity": "critical"},
    {"key": "data_exfiltration", "label_fa": "نشت یا استخراج غیرمجاز داده", "default_severity": "critical"},
    {"key": "ddos_flood", "label_fa": "حمله منع سرویس (DDoS / Flood)", "default_severity": "high"},
    {"key": "web_injection_sqli", "label_fa": "تزریق کد و حمله وب (SQLi / XSS)", "default_severity": "high"},
    {"key": "port_scan_recon", "label_fa": "پویش پورت و شناسایی شبکه", "default_severity": "medium"},
    {"key": "policy_violation", "label_fa": "نقض سیاست و کنترل امنیتی", "default_severity": "medium"},
    {"key": "suspicious_script", "label_fa": "اجرای اسکریپت یا PowerShell مشکوک", "default_severity": "high"},
    {"key": "vulnerability_exploit", "label_fa": "بهره‌برداری از آسیب‌پذیری (CVE)", "default_severity": "critical"},
    {"key": "firewall_network_block", "label_fa": "ترافیک مشکوک و مسدودسازی فایروال", "default_severity": "medium"},
    {"key": "rogue_device_usb", "label_fa": "تجهیزات جانبی یا USB غیرمجاز", "default_severity": "medium"},
    {"key": "other_security_event", "label_fa": "سایر رخدادهای امنیتی", "default_severity": "info"},
]

CATEGORY_LABEL_MAP = {c["key"]: c["label_fa"] for c in THREAT_CATEGORIES}

_CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("malware_ransomware", re.compile(r"(ransomware|trojan|worm|malware|virus|cryptolocker|باج.?افزار|بدافزار|تروجان|ویروس)", re.I), "تطابق کلیدواژه بدافزار/باج‌افزار"),
    ("phishing_social", re.compile(r"(phish|spearphish|spoof|bec\b|فیشینگ|جعل\s*هویت|لینک\s*جعلی)", re.I), "تطابق کلیدواژه فیشینگ"),
    ("brute_force_auth", re.compile(r"(brute\s*force|password\s*spray|failed\s*logon|4625|invalid\s*password|ورود\s*ناموفق|حمله\s*رمز)", re.I), "تطابق الگوی حمله احراز هویت"),
    ("privilege_escalation", re.compile(r"(privilege\s*escalat|sudo\s*abuse|admin\s*group|token\s*manip|ارتقای\s*دسترسی|دسترسی\s*غیرمجاز\s*ادمین)", re.I), "تطابق الگوی ارتقای سطح دسترسی"),
    ("lateral_movement", re.compile(r"(lateral\s*movement|psexec|wmiexec|rdp\s*pivot|pass\s*the\s*hash|حرکت\s*جانبی)", re.I), "تطابق الگوی حرکت جانبی"),
    ("data_exfiltration", re.compile(r"(exfiltrat|data\s*leak|dlp|bulk\s*export|نشت\s*اطلاعات|استخراج\s*داده|خروج\s*داده)", re.I), "تطابق الگوی نشت یا خروج داده"),
    ("ddos_flood", re.compile(r"(ddos|dos\s*attack|syn\s*flood|http\s*flood|منع\s*سرویس|ترافیک\s*انبوه)", re.I), "تطابق الگوی حمله منع سرویس"),
    ("web_injection_sqli", re.compile(r"(sql\s*inject|sqli|xss|rce|command\s*inject|path\s*travers|تزریق\s*کد|تزریق\s*اس.?کیو.?ال)", re.I), "تطابق الگوی تزریق وب/کد"),
    ("port_scan_recon", re.compile(r"(port\s*scan|nmap|recon|enumeration|پویش\s*پورت|اسکن\s*شبکه)", re.I), "تطابق الگوی پویش شبکه"),
    ("suspicious_script", re.compile(r"(powershell|encodedcommand|certutil|wscript|cscript|اسکریپت\s*مشکوک|پاورشل)", re.I), "تطابق الگوی اسکریپت مشکوک"),
    ("vulnerability_exploit", re.compile(r"(CVE-\d{4}-\d{4,7}|exploit|zero.?day|overflow|بهره.?برداری|آسیب.?پذیری)", re.I), "تطابق الگوی اکسپلویت/آسیب‌پذیری"),
    ("firewall_network_block", re.compile(r"(firewall|ips|ids|acl\s*deny|blocked\s*connection|فایروال|مسدودسازی\s*شبکه)", re.I), "تطابق الگوی فایروال و شبکه"),
    ("rogue_device_usb", re.compile(r"(usb\s*device|removable\s*media|rogue\s*mac|فلش\s*مموری|تجهیز\s*غیرمجاز)", re.I), "تطابق الگوی تجهیزات جانبی"),
    ("policy_violation", re.compile(r"(policy\s*violat|compliance|unauthorized\s*app|نقض\s*سیاست|تخطی)", re.I), "تطابق الگوی نقض سیاست"),
]

_STATUS_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("resolved_cleaned", re.compile(r"(پاکسازی\s*شد|برطرف\s*شد|رفع\s*شد|حذف\s*تهدید|موفقیت\s*آمیز\s*پاک|cleaned|remediated|resolved|patched)", re.I), "وضعیت برطرف/پاکسازی‌شده تشخیص داده شد"),
    ("blocked_quarantined", re.compile(r"(مسدود\s*شد|قرنطینه\s*شد|ایزوله\s*شد|بسته\s*شد|محدود\s*شد|blocked|quarantined|isolated|dropped|denied)", re.I), "وضعیت مسدود یا قرنطینه‌شده تشخیص داده شد"),
    ("false_positive", re.compile(r"(مثبت\s*کاذب|هشدار\s*کاذب|بی\s*خطر|تست\s*داخلی|false\s*positive|benign)", re.I), "وضعیت هشدار کاذب تشخیص داده شد"),
    ("active_threat", re.compile(r"(در\s*حال\s*حمله|نفوذ\s*موفق|فعال\s*است|آلوده\s*شد|تهدید\s*فعال|active\s*threat|compromised|infected|breach)", re.I), "وضعیت تهدید فعال تشخیص داده شد"),
    ("requires_review", re.compile(r"(نیازمند\s*بررسی|در\s*حال\s*بررسی|مشکوک|نامشخص|تحلیل\s*بیشتر|needs\s*review|suspicious|investigating|pending)", re.I), "وضعیت نیازمند بررسی تشخیص داده شد"),
]

_CVE_RE = re.compile(r"\b(CVE-\d{4}-\d{4,7})\b", re.I)
_SW_VER_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.\-]{2,32})[/:\s]+v?(\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9]+)?)\b"
)


def _norm_fa_text(txt: str) -> str:
    return (
        (txt or "")
        .replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
        .strip()
    )


def load_custom_security_rules(db: Session, org_id: str) -> list[SecurityClassificationRule]:
    return (
        db.query(SecurityClassificationRule)
        .filter(
            SecurityClassificationRule.organization_id == org_id,
            SecurityClassificationRule.is_active == True,
        )
        .order_by(SecurityClassificationRule.priority.asc(), SecurityClassificationRule.created_at.desc())
        .all()
    )


def classify_security_text(
    text: str,
    *,
    action: str = "",
    threat_name: str = "",
    rule_name: str = "",
    event_type: str = "",
    custom_rules: Optional[list[SecurityClassificationRule]] = None,
) -> dict[str, Any]:
    """Classifies security record into 15 threat categories, threat_status, software/version & reason."""
    combined = _norm_fa_text(" ".join([text or "", action or "", threat_name or "", rule_name or "", event_type or ""]))

    matched_cat: Optional[str] = None
    matched_status: Optional[str] = None
    severity_override: Optional[str] = None
    reasons: list[str] = []

    # 1) Apply Admin-defined custom NLP/Regex rules first
    for cr in custom_rules or []:
        try:
            rx = re.compile(_norm_fa_text(cr.pattern), re.I)
        except re.error:
            continue
        m = rx.search(combined)
        if not m:
            continue
        if cr.rule_kind == "category_map" and not matched_cat:
            matched_cat = cr.target_value
            reasons.append(f"قانون سفارشی «{cr.name}» ({m.group(0)})")
            if cr.severity_override:
                severity_override = cr.severity_override
        elif cr.rule_kind == "status_map" and not matched_status:
            matched_status = cr.target_value
            reasons.append(f"قانون وضعیت «{cr.name}» ({m.group(0)})")
            if cr.severity_override:
                severity_override = cr.severity_override

    # 2) Built-in 15-category classifier
    if not matched_cat:
        for cat_key, rx, reason_fa in _CATEGORY_PATTERNS:
            m = rx.search(combined)
            if m:
                matched_cat = cat_key
                reasons.append(f"{reason_fa}: «{m.group(0)}»")
                break
        if not matched_cat:
            matched_cat = "other_security_event"

    # 3) Built-in bilingual status classifier
    if not matched_status:
        for st_key, rx, reason_fa in _STATUS_PATTERNS:
            m = rx.search(combined)
            if m:
                matched_status = st_key
                reasons.append(f"{reason_fa}: «{m.group(0)}»")
                break

    if not matched_status:
        act_low = (action or "").lower()
        if act_low in ("block", "quarantine"):
            matched_status = "blocked_quarantined"
            reasons.append(f"استنتاج خودکار از اقدام {action}")
        elif act_low == "clean":
            matched_status = "resolved_cleaned"
            reasons.append("استنتاج خودکار از اقدام Clean")
        elif act_low == "failed":
            matched_status = "requires_review"
            reasons.append("رویداد ناموفق نیازمند بررسی")
        else:
            matched_status = "requires_review"

    # 4) Software / Version / CVE extraction
    cve_match = _CVE_RE.search(combined)
    sw_match = _SW_VER_RE.search(combined)
    sw_name = sw_match.group(1) if sw_match else ""
    sw_ver = sw_match.group(2) if sw_match else ""

    return {
        "threat_category": matched_cat,
        "threat_category_fa": CATEGORY_LABEL_MAP.get(matched_cat, matched_cat),
        "threat_status": matched_status,
        "severity_override": severity_override,
        "extraction_reason": " · ".join(reasons)[:215] if reasons else "طبقه‌بندی استاندارد بر اساس نوع رویداد",
        "cve_mentioned": cve_match.group(1).upper() if cve_match else None,
        "software_name": sw_name[:115],
        "software_version": sw_ver[:60],
    }
