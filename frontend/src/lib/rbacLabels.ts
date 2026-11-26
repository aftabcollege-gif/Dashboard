export const ROLE_META_FA: Record<string, { label: string; description: string }> = {
  super_admin: {
    label: "مدیر ارشد سامانه (Super Admin)",
    description: "دسترسی کامل و نامحدود به تمام تنظیمات، کاربران، نقش‌ها، داشبوردها، داده‌ها و ممیزی",
  },
  org_admin: {
    label: "مدیر سازمان",
    description: "مدیریت کاربران، منابع داده، مجموعه داده‌ها و داشبوردهای سازمان (به‌جز تغییر مدیر ارشد)",
  },
  security_admin: {
    label: "مدیر امنیت و دسترسی‌ها",
    description: "مدیریت RBAC، Active Directory، سیاست‌های RLS/CLS، لاگ‌های امنیت و ممیزی",
  },
  dashboard_admin: {
    label: "مدیر داشبوردها",
    description: "مدیریت داشبوردها، چیدمان‌ها، تم‌ها، دسترسی صفحات و ویجت‌ها",
  },
  dashboard_designer: {
    label: "طراح داشبورد و گزارش",
    description: "طراحی و ویرایش داشبوردها، ویجت‌ها، کوئری‌ها و کاوش جزئیات فرآیند",
  },
  data_analyst: {
    label: "تحلیل‌گر داده و فرآیند",
    description: "مشاهده و تحلیل داشبوردها، اجرای کوئری، دریل‌داون و هوش فرآیندی BPMN",
  },
  data_engineer: {
    label: "مهندس داده و ETL",
    description: "مدیریت منابع داده، مجموعه داده‌ها، خطوط انتقال ETL و دریافت لاگ امنیت",
  },
  report_viewer: {
    label: "کاربر مشاهده‌گر گزارش",
    description: "مشاهده فقط‌خواندنی داشبوردها با امکان فیلتر و خروجی PDF",
  },
  executive_viewer: {
    label: "کاربر مدیریتی / اجرایی",
    description: "نمای مدیریتی و شاخص‌های کلان با قابلیت دریل‌داون و دریافت گزارش",
  },
  department_manager: {
    label: "مدیر واحد سازمانی",
    description: "مشاهده و تحلیل داده‌ها و عملکرد واحد تحت سرپرستی مطابق سیاست‌های RLS",
  },
};

export const SCOPE_LABELS_FA: Record<string, string> = {
  organization: "سازمان",
  workspace: "فضای کاری",
  dashboard: "داشبورد",
  dashboard_page: "صفحه داشبورد",
  widget: "ویجت / نمودار",
  drill_down: "کاوش جزئیات (Drill-Down)",
  dataset: "مجموعه داده (Dataset)",
  data_source: "منبع داده (Data Source)",
  query: "کوئری و SQL",
  filter: "فیلترهای سراسری",
  export: "خروجی و گزارش",
  process_intel: "هوش فرآیندی (BPMN)",
  security_log: "عملیات امنیت (SOC)",
  admin: "مدیریت امنیت و کاربران",
};

export const ACTION_LABELS_FA: Record<string, string> = {
  view: "مشاهده",
  edit: "ویرایش",
  add: "افزودن / ایجاد",
  delete: "حذف",
  add_widget: "افزودن ویجت",
  delete_widget: "حذف ویجت",
  add_page: "افزودن صفحه",
  delete_page: "حذف صفحه",
  change_layout: "تغییر چیدمان",
  change_theme: "تغییر تم",
  manage_filters: "مدیریت فیلترها",
  refresh: "به‌روزرسانی داده",
  export: "دریافت خروجی",
  share: "اشتراک‌گذاری",
  manage_permissions: "مدیریت مجوزهای داشبورد",
  public_restricted_override: "مجوز انتشار عمومی داده محدود",
  change_chart_type: "تغییر نوع نمودار",
  change_style: "تغییر رنگ و ظاهر",
  change_query: "ویرایش کوئری",
  change_dataset: "تغییر منبع داده",
  change_filter: "ویرایش فیلتر ویجت",
  view_details: "مشاهده جزئیات",
  drill_down: "دریل‌داون به سطح پایین",
  drill_up: "بازگشت به سطح بالا",
  drill_through: "انتقال به صفحه جزئیات",
  drill_level_1: "سطح ۱ جزئیات",
  drill_level_2: "سطح ۲ جزئیات",
  drill_level_3: "سطح ۳ جزئیات",
  view_raw_records: "مشاهده رکوردهای خام",
  drill_through_target: "دسترسی به مقصد دریل‌ترو",
  view_schema: "مشاهده ساختار و ستون‌ها",
  view_data: "پیش‌نمایش داده‌ها",
  execute_query: "اجرای مستقیم کوئری",
  execute: "اجرای کوئری",
  export_pdf: "خروجی PDF",
  export_excel: "خروجی اکسل",
  export_csv: "خروجی CSV",
  export_png: "خروجی تصویر PNG",
  manage_mapping: "تنظیم نگاشت ستون‌های فرآیند",
  manage_actions: "مدیریت اقدامات اصلاحی",
  view_case_details: "مشاهده خط زمانی پرونده‌ها",
  ingest: "دریافت و پیکربندی لاگ امنیت",
  view_raw: "مشاهده متن خام لاگ‌ها",
  manage_alerts: "مدیریت و تریاژ هشدارها",
  manage_incidents: "مدیریت رخدادهای امنیتی (Incident)",
};

export function formatRoleLabel(role: { slug?: string; name?: string }, isFa: boolean): string {
  const slug = String(role?.slug || "").toLowerCase();
  if (isFa && ROLE_META_FA[slug]) return ROLE_META_FA[slug].label;
  return role?.name || slug || "نقش سفارشی";
}

export function formatScopeLabel(scope: string, isFa: boolean): string {
  return isFa ? SCOPE_LABELS_FA[scope] || scope : scope;
}

export function formatActionLabel(action: string, isFa: boolean): string {
  return isFa ? ACTION_LABELS_FA[action] || action : action;
}
