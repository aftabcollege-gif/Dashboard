export const APP_VERSION = "1.2.0";
export const PREVIOUS_VERSION = "1.1.0";
export const RELEASE_DATE = "2026-11-16";
export const SCHEMA_VERSION = "0007_enterprise_platform_v12";

export interface ReleaseNoteEntry {
  version: string;
  date: string;
  titleFa: string;
  titleEn: string;
  migrations: string[];
  breakingChanges: string[];
  upgradeNotesFa: string;
  rollbackNotesFa: string;
  highlightsFa: string[];
}

export const RELEASE_NOTES: ReleaseNoteEntry[] = [
  {
    version: "1.2.0",
    date: "2026-11-16",
    titleFa: "ارتقای جامع پلتفرم به هوش تجاری، کاوش فرآیند BPMN، عملیات امنیت و مدیریت دسترسی پیشرفته",
    titleEn: "Enterprise Analytics, BPMN Bottleneck Studio, Tier 1/2 SOC, Shared Data Sources & Persian RBAC",
    migrations: ["0007_enterprise_platform_v12 (افزایشی بدون حذف داده و سازگار با نسخه ۱.۱.۰)"],
    breakingChanges: [],
    upgradeNotesFa:
      "ارتقای افزایشی مستقیم روی نسخه ۱.۱.۰؛ کلیه داشبوردها، ویجت‌ها، دیتاست‌ها، کاربران، نقش‌ها و سیاست‌های امنیتی قبلی حفظ می‌شوند.",
    rollbackNotesFa:
      "در صورت نیاز به بازگشت، اجرای دستور `alembic downgrade 0006_process_security_intelligence` جداول جدید را بدون دست‌زدن به داده‌های نسخه ۱.۱.۰ بازمی‌گرداند.",
    highlightsFa: [
      "نسخه‌گذاری Semantic Versioning، صفحه درباره سامانه و مستند یادداشت انتشار",
      "فارسی‌سازی جامع مدیریت کاربران و دسترسی‌ها + پیش‌نمایش تغییر و محافظت از آخرین مدیر فعال",
      "اکسل چندشیت، انتخاب ستون‌ها، گزارش شمارش معتبر/نامعتبر، تحلیل Outlier و همبستگی ستون‌ها و Job ناهمزمان",
      "موتور فرمول گلوگاه BPMN، Tooltip یازده‌شاخصی، Drill-down پویا با Timeline و خروجی اکسل، و Drag & Drop گام‌ها",
      "عملیات امنیت Tier 1/2، موتور NLP/Regex فارسی وضعیت، ۱۵ رده تهدید و پایش آسیب‌پذیری CVE/CVSS",
      "لایه مشترک منابع داده با Join چندجدولی، بازطراحی فارسی ویرایشگر ویجت، KPI سفارشی و دستیار Q&A",
    ],
  },
  {
    version: "1.1.0",
    date: "2026-11-25",
    titleFa: "موتور ۱۰۰٪ SQL برای فایل‌های ۴۰۰٬۰۰۰+ ردیفی BPMN و سخت‌سازی امنیتی Stages 2–4",
    titleEn: "100%-row SQL-first Multi-Process BPMN, Stage 2–4 Security & Desktop E2E",
    migrations: ["0002_stage3_external_identity_scope .. 0006_process_security_intelligence"],
    breakingChanges: [],
    upgradeNotesFa: "افزوده شدن Alembic، ایزوله‌سازی چندمستأجره، محافظت SSRF/AST و دریل‌داون SQL کارتابل BPMN.",
    rollbackNotesFa: "بازگشت مرحله‌ای بین revisionهای 0002 تا 0006.",
    highlightsFa: [
      "پشتیبانی ۴۰۰٬۰۰۰+ رکورد اکسل و ستون‌های فارسی در DuckDB SQL بدون برش خاموش",
      "پایش میان‌دامنه‌ای فرآیند و لاگ امنیت و بسته‌بندی ویندوز",
    ],
  },
  {
    version: "1.0.0",
    date: "2026-11-16",
    titleFa: "نسخه پایه نکسوس BI سازمانی",
    titleEn: "Initial NexusBI Enterprise Platform",
    migrations: ["0001_initial_baseline"],
    breakingChanges: [],
    upgradeNotesFa: "راه‌اندازی پایه FastAPI + DuckDB + Next.js + ECharts + RBAC.",
    rollbackNotesFa: "نسخه پایه.",
    highlightsFa: ["داشبوردساز پویا، ۳۰ نمودار، ETL، Active Directory و RLS/CLS"],
  },
];
