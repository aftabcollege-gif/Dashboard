"""Centralized Semantic Versioning & Release Continuity Metadata for NexusBI Enterprise."""

APP_NAME = "Enterprise Intelligence BI Platform"
APP_VERSION = "1.2.0"
PREVIOUS_VERSION = "1.1.0"
API_VERSION = "1.2.0"
RELEASE_DATE = "2026-11-16"
SCHEMA_HEAD_TARGET = "0007_enterprise_platform_v12"

RELEASE_NOTES = [
    {
        "version": "1.2.0",
        "date": "2026-11-16",
        "title_fa": "ارتقای جامع پلتفرم به هوش تجاری، کاوش فرآیند BPMN، عملیات امنیت و مدیریت دسترسی پیشرفته",
        "title_en": "Enterprise Analytics, Customizable BPMN Bottleneck Studio, Tier 1/2 SOC, Shared Data Sources & Persian RBAC",
        "breaking_changes": [],
        "migrations": ["0007_enterprise_platform_v12 (additive; preserves all existing data, dashboards, datasets, and users)"],
        "upgrade_notes_fa": "ارتقای کاملاً سازگار با نسخه ۱.۱.۰؛ تنها جداول و ستون‌های افزایشی اضافه می‌شوند و داده‌ها، داشبوردها و مجوزهای قبلی بدون تغییر حفظ می‌گردند.",
        "rollback_notes_fa": "بازگشت امن با اجرای `alembic downgrade 0006_process_security_intelligence` بدون حذف جداول هسته‌ای ۱.۰/۱.۱.",
        "highlights_fa": [
            "نسخه‌گذاری یکپارچه، نمایش نسخه در رابط «درباره سامانه» و یادداشت انتشار رسمی",
            "فارسی‌سازی کامل مدیریت کاربران، نقش‌ها، گروه‌ها و ماتریس دسترسی همراه با محافظت از آخرین مدیر فعال، پیش‌نمایش تغییرات و ثبت دلیل در Audit Log",
            "پشتیبانی اکسل چندشیت، انتخاب ستون‌ها، گزارش شمارش معتبر/نامعتبر، تشخیص Outlier و همبستگی ستون‌ها در DuckDB SQL و کارهای ناهمزمان با درصد پیشرفت واقعی",
            "موتور فرمول گلوگاه BPMN با اعتبارسنجی AST، Tooltip تحلیلی ۱۱ شاخصی، Drill-down کامل با Timeline و خروجی اکسل، و تغییر ترتیب گام‌ها با Drag & Drop",
            "داشبورد عملیات امنیت Tier 1 و Tier 2، موتور NLP/Regex فارسی وضعیت لاگ، طبقه‌بندی ۱۵ تهدید، پایش CVE/CVSS و گردش‌کار ۷ مرحله‌ای Incident",
            "زیرساخت مشترک منابع داده با Join چندجدولی، بازطراحی فارسی ویرایشگر ویجت با Tooltip و Undo/Redo، KPIهای سفارشی و دستیار Q&A چنددامنه‌ای",
        ],
    },
    {
        "version": "1.1.0",
        "date": "2026-11-25",
        "title_fa": "موتور SQL-first برای فایل‌های ۴۰۰٬۰۰۰+ ردیفی BPMN، هوش فرآیندی و سخت‌سازی امنیتی Stages 2–4",
        "title_en": "100%-row SQL-first Multi-Process BPMN (400k+ scale), Stage 2–4 Security Hardening & Desktop Packaging",
        "breaking_changes": [],
        "migrations": ["0002_stage3_external_identity_scope .. 0006_process_security_intelligence"],
        "upgrade_notes_fa": "مهاجرت کامل به Alembic، ایزوله‌سازی چندمستأجره، محافظت SSRF/AST و موتور DuckDB سخت‌سازی‌شده.",
        "rollback_notes_fa": "بازگشت مرحله‌ای از طریق revisionهای ۰002 تا 0006 Alembic.",
        "highlights_fa": [
            "تحلیل ۱۰۰٪ ردیف‌های اکسل حجیم بدون برش ۲۵ هزار یا ۱۰۰۰ ردیفی",
            "کشف چندفرآیندی و دریل‌داون کارتابل گام‌های BPMN در DuckDB SQL",
            "سخت‌سازی امنیتی DuckDB، کنترل‌های CSRF/Cookie، ایزوله‌سازی Org و تست‌های E2E دسکتاپ",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-11-16",
        "title_fa": "نسخه پایه پلتفرم هوش تجاری نکسوس BI",
        "title_en": "Initial Enterprise Intelligence BI Platform Baseline",
        "breaking_changes": [],
        "migrations": ["0001_initial_baseline"],
        "upgrade_notes_fa": "نسخه اولیه شامل DuckDB Warehouse، ۳۰ نوع نمودار، سازنده داشبورد، ETL و Active Directory.",
        "rollback_notes_fa": "نصب پایه بدون پیش‌نیاز قبلی.",
        "highlights_fa": [
            "انبار داده DuckDB و ۳۰ نوع نمودار تعاملی ECharts و BPMN",
            "مدیریت کاربران، Active Directory، RLS/CLS و خروجی گزارش",
        ],
    },
]
