"""
Enterprise Persian BI SQL Formula Engine:
- Full Persian SQL Function Catalog aligned 1:1 with DuckDB runtime functions
- Strict sqlglot AST allowlist, argument arity, data-type, and CLS/RBAC validation
- Dependency resolution & DFS cycle detection across Calculated Columns, Calculated Measures & Semantic Metrics
- Pure DuckDB SQL Jalali (Solar Hijri) calendar expansion & bidirectional date parsing
- Division-by-zero safety (`NULLIF`) & decimal precision protection
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any, Optional
import sqlglot
from sqlglot import exp
from sqlalchemy.orm import Session
from app.core.permissions import permission_resolver
from app.core.rls_cls import build_data_security_context
from app.core.sql_guard import SAFE_FORMULA_FUNCS, _node_func_name, validate_formula_ast
from app.models.data import Dataset, DatasetField, SemanticMetric
from app.models.identity import User

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

AGGREGATE_FUNCS = {
    "sum", "avg", "count", "count_distinct", "min", "max", "median",
    "stddev", "stddev_samp", "stddev_pop", "variance", "var_samp", "var_pop",
    "percentile_cont", "quantile_cont", "corr", "skewness", "kurtosis",
    "approx_distinct", "approx_count_distinct", "arg_max", "arg_min",
    "first_value", "last_value", "any_value",
    "percentage", "running_total", "yoy", "mom", "ytd", "mtd", "rank",
}

NUMERIC_TYPES = {"integer", "int", "bigint", "float", "double", "decimal", "numeric", "number", "real"}
STRING_TYPES = {"string", "varchar", "text", "char"}
DATE_TYPES = {"date", "datetime", "timestamp", "timestamptz"}


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666
        + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd
        + g_d_m[gm - 1]
    )
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd + (
        (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    )
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def parse_jalali_or_gregorian_date(raw: str) -> Optional[date]:
    txt = str(raw or "").strip().translate(PERSIAN_DIGITS)
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", txt)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        if 1200 <= y <= 1650:
            gy, gm, gd = jalali_to_gregorian(y, mo, d)
            return date(gy, gm, gd)
        return date(y, mo, d)
    except ValueError:
        return None


def _jalali_sql_parts(date_expr_sql: str) -> tuple[str, str, str, str]:
    d = f"TRY_CAST({date_expr_sql} AS DATE)"
    days0 = f"(582928 + DATE_DIFF('day', CAST('0622-03-22' AS DATE), {d}))"
    d_mod = f"({days0} % 12053)"
    rem1 = f"({d_mod} % 1461)"
    jy = f"(-1595 + 33 * ({days0} // 12053) + 4 * ({d_mod} // 1461) + (CASE WHEN {rem1} > 365 THEN (({rem1} - 1) // 365) ELSE 0 END))"
    d_in_y = f"(CASE WHEN {rem1} > 365 THEN (({rem1} - 1) % 365) ELSE {rem1} END)"
    jm = f"(CASE WHEN {d_in_y} < 186 THEN 1 + ({d_in_y} // 31) ELSE 7 + (({d_in_y} - 186) // 30) END)"
    jd = f"(CASE WHEN {d_in_y} < 186 THEN 1 + ({d_in_y} % 31) ELSE 1 + (({d_in_y} - 186) % 30) END)"
    jq = f"(1 + ((({jm}) - 1) // 3))"
    return jy, jm, jq, jd


def expand_jalali_sql_calls(sql_expr: str) -> str:
    """Replace JALALI_YEAR(x), JALALI_MONTH(x), JALALI_QUARTER(x), JALALI_DATE(x) with pure DuckDB SQL."""
    try:
        tree = sqlglot.parse_one(f"SELECT {sql_expr} AS _m", read="duckdb")
    except Exception:
        return sql_expr

    def _transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Anonymous):
            fn = (node.name or "").strip().lower()
            if fn in ("jalali_year", "jalali_month", "jalali_quarter", "jalali_date"):
                if len(node.expressions) != 1:
                    raise ValueError(f"Function {fn.upper()} requires exactly 1 date argument")
                inner_sql = node.expressions[0].sql(dialect="duckdb")
                jy, jm, jq, jd = _jalali_sql_parts(inner_sql)
                if fn == "jalali_year":
                    rep = jy
                elif fn == "jalali_month":
                    rep = jm
                elif fn == "jalali_quarter":
                    rep = jq
                else:
                    rep = (
                        f"CONCAT(CAST({jy} AS VARCHAR), '/', "
                        f"RIGHT(CONCAT('0', CAST({jm} AS VARCHAR)), 2), '/', "
                        f"RIGHT(CONCAT('0', CAST({jd} AS VARCHAR)), 2))"
                    )
                return sqlglot.parse_one(rep, read="duckdb")
        return node

    transformed = tree.transform(_transform)
    alias_node = transformed.expressions[0]
    inner = alias_node.this if isinstance(alias_node, exp.Alias) else alias_node
    return inner.sql(dialect="duckdb")


# Full Persian SQL Function Catalog aligned 1:1 with DuckDB & FormulaCompiler
SQL_FUNCTION_CATALOG: list[dict[str, Any]] = [
    # Aggregate
    {
        "id": "SUM", "sql_name": "SUM", "fa_name": "جمع", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "SUM(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "مجموع مقادیر غیرخالی یک ستون عددی را محاسبه می‌کند.",
        "simple_example": "SUM(amount)",
        "combined_example": "SUM(amount) - SUM(cost)",
        "common_errors_fa": "استفاده روی ستون متنی یا استفاده در ستون محاسباتی سطح ردیف بدون GROUP BY.",
        "engine_notes_fa": "در DuckDB برای جلوگیری از سرریز، روی خروجی جمع‌های بزرگ به DOUBLE تبدیل می‌شود و مقادیر NULL نادیده گرفته می‌شوند.",
    },
    {
        "id": "AVG", "sql_name": "AVG", "fa_name": "میانگین", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "AVG(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "میانگین حسابی مقادیر غیرخالی را محاسبه می‌کند.",
        "simple_example": "AVG(amount)",
        "combined_example": "ROUND(AVG(amount), 2)",
        "common_errors_fa": "مقادیر NULL در مخرج میانگین شمارش نمی‌شوند؛ در صورت نیاز از COALESCE(col, 0) استفاده کنید.",
        "engine_notes_fa": "خروجی در DuckDB همیشه از نوع DOUBLE است.",
    },
    {
        "id": "COUNT", "sql_name": "COUNT", "fa_name": "تعداد", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "COUNT(ستون_یا_*)", "min_args": 1, "max_args": 1, "arg_types": ["any"], "return_type": "integer",
        "description_fa": "تعداد ردیف‌ها (*) یا تعداد مقادیر غیرخالی یک ستون را می‌شمارد.",
        "simple_example": "COUNT(*)",
        "combined_example": "COUNT(*) - COUNT(discount)",
        "common_errors_fa": "تفاوت COUNT(*) با COUNT(col): اولی ردیف‌های NULL را هم می‌شمارد.",
        "engine_notes_fa": "در DuckDB با سرعت برداری (Vectorized) اجرا می‌شود.",
    },
    {
        "id": "COUNT_DISTINCT", "sql_name": "COUNT(DISTINCT ...)", "fa_name": "تعداد مقادیر یکتا", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "COUNT(DISTINCT ستون)", "min_args": 1, "max_args": 1, "arg_types": ["any"], "return_type": "integer",
        "description_fa": "تعداد مقادیر متمایز و غیرتکراری را شمارش می‌کند (مثلاً مشتریان یکتا).",
        "simple_example": "COUNT(DISTINCT customer_id)",
        "combined_example": "SUM(amount) / NULLIF(COUNT(DISTINCT customer_id), 0)",
        "common_errors_fa": "فراموش کردن NULLIF هنگام تقسیم بر تعداد یکتا که ممکن است صفر شود.",
        "engine_notes_fa": "در DuckDB دقیق و با حافظه بهینه اجرا می‌شود.",
    },
    {
        "id": "MIN", "sql_name": "MIN", "fa_name": "حداقل", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "MIN(ستون)", "min_args": 1, "max_args": 1, "arg_types": ["comparable"], "return_type": "same",
        "description_fa": "کوچک‌ترین مقدار عددی، متنی یا قدیمی‌ترین تاریخ را برمی‌گرداند.",
        "simple_example": "MIN(amount)",
        "combined_example": "MAX(amount) - MIN(amount)",
        "common_errors_fa": "مقایسه ستون متنی حاوی عدد به جای عدد واقعی.",
        "engine_notes_fa": "برای تاریخ‌ها اولین تاریخ را برمی‌گرداند.",
    },
    {
        "id": "MAX", "sql_name": "MAX", "fa_name": "حداکثر", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "MAX(ستون)", "min_args": 1, "max_args": 1, "arg_types": ["comparable"], "return_type": "same",
        "description_fa": "بزرگ‌ترین مقدار یا جدیدترین تاریخ را برمی‌گرداند.",
        "simple_example": "MAX(amount)",
        "combined_example": "DATE_DIFF('day', MIN(order_date), MAX(order_date))",
        "common_errors_fa": "تفسیر اشتباه MAX به عنوان آخرین مقدار زمانی وقتی ستون تاریخ نیست.",
        "engine_notes_fa": "مقادیر NULL نادیده گرفته می‌شوند.",
    },
    {
        "id": "MEDIAN", "sql_name": "MEDIAN", "fa_name": "میانه", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "MEDIAN(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "مقدار میانه (صدک ۵۰٪) را محاسبه می‌کند که در برابر داده‌های پرت مقاوم است.",
        "simple_example": "MEDIAN(amount)",
        "combined_example": "AVG(amount) - MEDIAN(amount)",
        "common_errors_fa": "استفاده روی ستون متنی باعث خطای نوع داده می‌شود.",
        "engine_notes_fa": "تابع بومی DuckDB بدون نیاز به اکستنشن اضافی.",
    },
    {
        "id": "STDDEV", "sql_name": "STDDEV", "fa_name": "انحراف معیار", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "STDDEV(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "پراکندگی داده‌ها نسبت به میانگین را محاسبه می‌کند.",
        "simple_example": "STDDEV(amount)",
        "combined_example": "ROUND(STDDEV(amount) / NULLIF(AVG(amount), 0) * 100.0, 2)",
        "common_errors_fa": "برای گروه‌های تک‌ردیفه مقدار NULL برمی‌گرداند.",
        "engine_notes_fa": "معادل STDDEV_SAMP در DuckDB.",
    },
    {
        "id": "VARIANCE", "sql_name": "VARIANCE", "fa_name": "واریانس", "category": "aggregate", "category_fa": "توابع تجمیعی",
        "signature": "VARIANCE(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "واریانس نمونه‌ای یک ستون عددی را محاسبه می‌کند.",
        "simple_example": "VARIANCE(amount)",
        "combined_example": "SQRT(COALESCE(VARIANCE(amount), 0))",
        "common_errors_fa": "برای گروه‌های دارای یک رکورد NULL برمی‌گرداند.",
        "engine_notes_fa": "تابع بومی DuckDB.",
    },
    # Math
    {
        "id": "ABS", "sql_name": "ABS", "fa_name": "قدر مطلق", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "ABS(عدد)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "مقدار بدون علامت (مثبت) یک عدد یا اختلاف را برمی‌گرداند.",
        "simple_example": "ABS(amount - cost)",
        "combined_example": "SUM(ABS(amount - cost))",
        "common_errors_fa": "ارسال متن غیرعددی به جای عدد.",
        "engine_notes_fa": "هم در سطح ردیف و هم درون تجمیع قابل استفاده است.",
    },
    {
        "id": "ROUND", "sql_name": "ROUND", "fa_name": "گرد کردن", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "ROUND(عدد, تعداد_اعشار)", "min_args": 1, "max_args": 2, "arg_types": ["numeric", "integer"], "return_type": "double",
        "description_fa": "عدد را به نزدیک‌ترین مقدار با تعداد ارقام اعشار مشخص گرد می‌کند.",
        "simple_example": "ROUND(amount, 2)",
        "combined_example": "ROUND((SUM(amount) - SUM(cost)) * 100.0 / NULLIF(SUM(amount), 0), 1)",
        "common_errors_fa": "ارسال پارامتر دوم غیرصحیح.",
        "engine_notes_fa": "در DuckDB آرگومان دوم اختیاری و پیش‌فرض آن ۰ است.",
    },
    {
        "id": "CEIL", "sql_name": "CEIL", "fa_name": "سقف عدد", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "CEIL(عدد)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "کوچک‌ترین عدد صحیح بزرگ‌تر یا مساوی مقدار ورودی را برمی‌گرداند.",
        "simple_example": "CEIL(amount / 1000.0)",
        "combined_example": "CEIL(AVG(quantity))",
        "common_errors_fa": "برای اعداد منفی به سمت صفر گرد می‌شود (مثلاً CEIL(-1.2) = -1).",
        "engine_notes_fa": "معادل CEILING.",
    },
    {
        "id": "FLOOR", "sql_name": "FLOOR", "fa_name": "کف عدد", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "FLOOR(عدد)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "بزرگ‌ترین عدد صحیح کوچک‌تر یا مساوی مقدار ورودی را برمی‌گرداند.",
        "simple_example": "FLOOR(amount / 1000.0)",
        "combined_example": "FLOOR(MEDIAN(quantity))",
        "common_errors_fa": "تفاوت با گرد کردن معمولی برای اعداد اعشاری بالا.",
        "engine_notes_fa": "تابع بومی DuckDB.",
    },
    {
        "id": "POWER", "sql_name": "POWER", "fa_name": "توان", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "POWER(پایه, توان)", "min_args": 2, "max_args": 2, "arg_types": ["numeric", "numeric"], "return_type": "double",
        "description_fa": "پایه را به توان عدد دوم می‌رساند.",
        "simple_example": "POWER(quantity, 2)",
        "combined_example": "ROUND(POWER(1.0 + 0.15, 3), 2)",
        "common_errors_fa": "وارد کردن فقط یک آرگومان.",
        "engine_notes_fa": "خروجی DOUBLE است.",
    },
    {
        "id": "SQRT", "sql_name": "SQRT", "fa_name": "ریشه دوم", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "SQRT(عدد_نامنفی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "جذر عدد غیرمنفی را برمی‌گرداند.",
        "simple_example": "SQRT(ABS(amount))",
        "combined_example": "ROUND(SQRT(VARIANCE(amount)), 2)",
        "common_errors_fa": "ورودی منفی باعث خطا می‌شود؛ از ABS یا CASE برای اعداد منفی استفاده کنید.",
        "engine_notes_fa": "برای مقادیر منفی در DuckDB خطای Domain رخ می‌دهد.",
    },
    {
        "id": "MOD", "sql_name": "MOD", "fa_name": "باقیمانده تقسیم", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "MOD(مقسوم, مقسوم‌علیه)", "min_args": 2, "max_args": 2, "arg_types": ["numeric", "numeric"], "return_type": "integer",
        "description_fa": "باقیمانده تقسیم دو عدد را برمی‌گرداند.",
        "simple_example": "MOD(quantity, 2)",
        "combined_example": "MOD(CAST(quantity AS BIGINT), NULLIF(2, 0))",
        "common_errors_fa": "صفر بودن آرگومان دوم باعث خطا می‌شود.",
        "engine_notes_fa": "معادل عملگر %.",
    },
    {
        "id": "SIGN", "sql_name": "SIGN", "fa_name": "علامت عدد", "category": "math", "category_fa": "توابع ریاضی",
        "signature": "SIGN(عدد)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "integer",
        "description_fa": "برای عدد مثبت ۱، منفی -۱ و صفر ۰ برمی‌گرداند.",
        "simple_example": "SIGN(profit)",
        "combined_example": "CASE WHEN SIGN(SUM(amount) - SUM(cost)) > 0 THEN 'سودآور' ELSE 'زیان‌ده' END",
        "common_errors_fa": "استفاده مستقیم روی متن.",
        "engine_notes_fa": "خروجی عدد صحیح {-1, 0, 1}.",
    },
    # String
    {
        "id": "CONCAT", "sql_name": "CONCAT", "fa_name": "اتصال متن", "category": "string", "category_fa": "توابع متنی",
        "signature": "CONCAT(متن۱, متن۲, ...)", "min_args": 1, "max_args": 20, "arg_types": ["any"], "return_type": "string",
        "description_fa": "دو یا چند متن یا ستون را به هم می‌چسباند.",
        "simple_example": "CONCAT(region, ' - ', category)",
        "combined_example": "CONCAT(UPPER(TRIM(region)), ': ', CAST(quantity AS VARCHAR))",
        "common_errors_fa": "استفاده از عملگر + برای اتصال متن در SQL اشتباه است؛ از CONCAT یا || استفاده کنید.",
        "engine_notes_fa": "در DuckDB مقادیر NULL را در CONCAT به رشته خالی تبدیل می‌کند.",
    },
    {
        "id": "UPPER", "sql_name": "UPPER", "fa_name": "حروف بزرگ", "category": "string", "category_fa": "توابع متنی",
        "signature": "UPPER(متن)", "min_args": 1, "max_args": 1, "arg_types": ["string"], "return_type": "string",
        "description_fa": "تمام حروف انگلیسی متن را به حروف بزرگ تبدیل می‌کند.",
        "simple_example": "UPPER(region)",
        "combined_example": "UPPER(TRIM(region))",
        "common_errors_fa": "اجرا روی ستون عددی بدون CAST.",
        "engine_notes_fa": "سازگار با UTF-8.",
    },
    {
        "id": "LOWER", "sql_name": "LOWER", "fa_name": "حروف کوچک", "category": "string", "category_fa": "توابع متنی",
        "signature": "LOWER(متن)", "min_args": 1, "max_args": 1, "arg_types": ["string"], "return_type": "string",
        "description_fa": "تمام حروف انگلیسی متن را به حروف کوچک تبدیل می‌کند.",
        "simple_example": "LOWER(category)",
        "combined_example": "LOWER(TRIM(category))",
        "common_errors_fa": "نرمال‌سازی نکردن متن قبل از مقایسه.",
        "engine_notes_fa": "مفید برای یکسان‌سازی مقادیر دسته‌ای.",
    },
    {
        "id": "TRIM", "sql_name": "TRIM", "fa_name": "حذف فاصله اضافی", "category": "string", "category_fa": "توابع متنی",
        "signature": "TRIM(متن)", "min_args": 1, "max_args": 2, "arg_types": ["string"], "return_type": "string",
        "description_fa": "فاصله‌های خالی ابتدا و انتهای متن را حذف می‌کند.",
        "simple_example": "TRIM(region)",
        "combined_example": "CASE WHEN LENGTH(TRIM(COALESCE(region, ''))) = 0 THEN 'نامشخص' ELSE TRIM(region) END",
        "common_errors_fa": "باقی ماندن فاصله در انتهای کدها که باعث گروه شدن جداگانه در نمودار می‌شود.",
        "engine_notes_fa": "همراه با LTRIM و RTRIM پشتیبانی می‌شود.",
    },
    {
        "id": "LENGTH", "sql_name": "LENGTH", "fa_name": "طول متن", "category": "string", "category_fa": "توابع متنی",
        "signature": "LENGTH(متن)", "min_args": 1, "max_args": 1, "arg_types": ["string"], "return_type": "integer",
        "description_fa": "تعداد کاراکترهای یک رشته متنی را برمی‌گرداند.",
        "simple_example": "LENGTH(category)",
        "combined_example": "AVG(LENGTH(TRIM(category)))",
        "common_errors_fa": "ارسال ستون عددی بدون تبدیل به VARCHAR.",
        "engine_notes_fa": "طول کاراکتری UTF-8 را برمی‌گرداند.",
    },
    {
        "id": "SUBSTRING", "sql_name": "SUBSTRING", "fa_name": "برش متن", "category": "string", "category_fa": "توابع متنی",
        "signature": "SUBSTRING(متن, شروع, طول)", "min_args": 2, "max_args": 3, "arg_types": ["string", "integer", "integer"], "return_type": "string",
        "description_fa": "بخشی از متن را از موقعیت شروع (از ۱) جدا می‌کند.",
        "simple_example": "SUBSTRING(CAST(order_date AS VARCHAR), 1, 7)",
        "combined_example": "UPPER(SUBSTRING(TRIM(region), 1, 3))",
        "common_errors_fa": "شروع اندیس در SQL از ۱ است نه ۰.",
        "engine_notes_fa": "معادل SUBSTR در DuckDB.",
    },
    {
        "id": "REPLACE", "sql_name": "REPLACE", "fa_name": "جایگزینی متن", "category": "string", "category_fa": "توابع متنی",
        "signature": "REPLACE(متن, متن_قدیم, متن_جدید)", "min_args": 3, "max_args": 3, "arg_types": ["string", "string", "string"], "return_type": "string",
        "description_fa": "تمام رخدادهای یک زیررشته را با متن جدید جایگزین می‌کند.",
        "simple_example": "REPLACE(region, ' ', '_')",
        "combined_example": "TRIM(REPLACE(category, '-', ' '))",
        "common_errors_fa": "فراموش کردن آرگومان سوم (حتی برای حذف باید '' بدهید).",
        "engine_notes_fa": "حساس به حروف کوچک و بزرگ.",
    },
    {
        "id": "LEFT", "sql_name": "LEFT", "fa_name": "ابتدای متن", "category": "string", "category_fa": "توابع متنی",
        "signature": "LEFT(متن, تعداد)", "min_args": 2, "max_args": 2, "arg_types": ["string", "integer"], "return_type": "string",
        "description_fa": "N کاراکتر ابتدایی رشته را برمی‌گرداند.",
        "simple_example": "LEFT(region, 3)",
        "combined_example": "UPPER(LEFT(TRIM(region), 2))",
        "common_errors_fa": "ارسال طول منفی.",
        "engine_notes_fa": "همراه با RIGHT پشتیبانی می‌شود.",
    },
    {
        "id": "RIGHT", "sql_name": "RIGHT", "fa_name": "انتهای متن", "category": "string", "category_fa": "توابع متنی",
        "signature": "RIGHT(متن, تعداد)", "min_args": 2, "max_args": 2, "arg_types": ["string", "integer"], "return_type": "string",
        "description_fa": "N کاراکتر انتهایی رشته را برمی‌گرداند.",
        "simple_example": "RIGHT(CAST(order_date AS VARCHAR), 2)",
        "combined_example": "RIGHT(TRIM(category), 4)",
        "common_errors_fa": "ارسال نوع داده غیراستاندارد بدون CAST.",
        "engine_notes_fa": "تابع بومی DuckDB.",
    },
    # Date & Time
    {
        "id": "CURRENT_DATE", "sql_name": "CURRENT_DATE", "fa_name": "تاریخ امروز", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "CURRENT_DATE", "min_args": 0, "max_args": 0, "arg_types": [], "return_type": "date",
        "description_fa": "تاریخ روز جاری سرور را برمی‌گرداند.",
        "simple_example": "CURRENT_DATE",
        "combined_example": "DATE_DIFF('day', CAST(order_date AS DATE), CURRENT_DATE)",
        "common_errors_fa": "قرار دادن پرانتز CURRENT_DATE() در برخی گویش‌ها نیازی نیست؛ بدون پرانتز بنویسید.",
        "engine_notes_fa": "در DuckDB مقدار DATE برمی‌گرداند.",
    },
    {
        "id": "DATE_TRUNC", "sql_name": "DATE_TRUNC", "fa_name": "برش بازه تاریخ", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "DATE_TRUNC('month'|'week'|'quarter'|'year', تاریخ)", "min_args": 2, "max_args": 2, "arg_types": ["string", "date"], "return_type": "date",
        "description_fa": "تاریخ را به ابتدای ماه، هفته، فصل یا سال گرد می‌کند (بسیار پرکاربرد در روند زمانی).",
        "simple_example": "DATE_TRUNC('month', CAST(order_date AS DATE))",
        "combined_example": "STRFTIME(DATE_TRUNC('month', CAST(order_date AS DATE)), '%Y-%m')",
        "common_errors_fa": "جابه جا نوشتن ترتیب پارامترها: ابتدا واحد ('month') و سپس ستون تاریخ.",
        "engine_notes_fa": "در DuckDB دقیق‌ترین راه برای گروه‌بندی ماهانه/فصلی است.",
    },
    {
        "id": "EXTRACT", "sql_name": "EXTRACT", "fa_name": "استخراج جزء تاریخ", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "EXTRACT(YEAR|MONTH|QUARTER|DAY FROM تاریخ)", "min_args": 1, "max_args": 2, "arg_types": ["date"], "return_type": "integer",
        "description_fa": "سال، ماه، فصل یا روز را از یک ستون تاریخ استخراج می‌کند.",
        "simple_example": "EXTRACT(YEAR FROM CAST(order_date AS DATE))",
        "combined_example": "EXTRACT(QUARTER FROM CAST(order_date AS DATE))",
        "common_errors_fa": "فراموش کردن کلیدواژه FROM داخل پرانتز EXTRACT.",
        "engine_notes_fa": "همچنین DATE_PART('year', col) پشتیبانی می‌شود.",
    },
    {
        "id": "DATE_DIFF", "sql_name": "DATE_DIFF", "fa_name": "اختلاف دو تاریخ", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "DATE_DIFF('day'|'month'|'year', تاریخ_اول, تاریخ_دوم)", "min_args": 3, "max_args": 3, "arg_types": ["string", "date", "date"], "return_type": "integer",
        "description_fa": "تعداد روز، ماه یا سال بین دو تاریخ را محاسبه می‌کند.",
        "simple_example": "DATE_DIFF('day', CAST(order_date AS DATE), CURRENT_DATE)",
        "combined_example": "AVG(DATE_DIFF('day', CAST(start_date AS DATE), CAST(end_date AS DATE)))",
        "common_errors_fa": "برعکس نوشتن تاریخ اول و دوم که نتیجه منفی می‌دهد.",
        "engine_notes_fa": "در DuckDB پارامتر اول واحد زمانی است ('day', 'hour', 'month', 'year').",
    },
    {
        "id": "STRFTIME", "sql_name": "STRFTIME", "fa_name": "قالب‌بندی تاریخ", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "STRFTIME(تاریخ, '%Y-%m-%d')", "min_args": 2, "max_args": 2, "arg_types": ["date", "string"], "return_type": "string",
        "description_fa": "تاریخ را به متن با الگوی دلخواه تبدیل می‌کند.",
        "simple_example": "STRFTIME(CAST(order_date AS DATE), '%Y-%m')",
        "combined_example": "STRFTIME(DATE_TRUNC('month', CAST(order_date AS DATE)), '%Y-%m')",
        "common_errors_fa": "استفاده از ستون متنی بدون CAST به DATE.",
        "engine_notes_fa": "الگوهای استاندارد %Y، %m، %d را می‌پذیرد.",
    },
    {
        "id": "JALALI_YEAR", "sql_name": "JALALI_YEAR", "fa_name": "سال شمسی", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "JALALI_YEAR(تاریخ_میلادی)", "min_args": 1, "max_args": 1, "arg_types": ["date"], "return_type": "integer",
        "description_fa": "سال دقیق هجری شمسی (مانند ۱۴۰۴) را مستقیماً در کوئری DuckDB محاسبه می‌کند.",
        "simple_example": "JALALI_YEAR(order_date)",
        "combined_example": "CONCAT(CAST(JALALI_YEAR(order_date) AS VARCHAR), '-Q', CAST(JALALI_QUARTER(order_date) AS VARCHAR))",
        "common_errors_fa": "ارسال متن نامعتبر به عنوان تاریخ.",
        "engine_notes_fa": "کامپایلر به‌صورت خودکار به محاسبه دقیق ۳۳ ساله در SQL بومی DuckDB تبدیل می‌کند.",
    },
    {
        "id": "JALALI_MONTH", "sql_name": "JALALI_MONTH", "fa_name": "ماه شمسی", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "JALALI_MONTH(تاریخ_میلادی)", "min_args": 1, "max_args": 1, "arg_types": ["date"], "return_type": "integer",
        "description_fa": "شماره ماه شمسی (۱ تا ۱۲) را محاسبه می‌کند.",
        "simple_example": "JALALI_MONTH(order_date)",
        "combined_example": "JALALI_DATE(order_date)",
        "common_errors_fa": "ستون ورودی باید تاریخ یا رشته YYYY-MM-DD معتبر باشد.",
        "engine_notes_fa": "کامپایل خالص به SQL بومی DuckDB بدون اکستنشن.",
    },
    {
        "id": "JALALI_DATE", "sql_name": "JALALI_DATE", "fa_name": "تاریخ کامل شمسی", "category": "date_time", "category_fa": "توابع تاریخ و زمان",
        "signature": "JALALI_DATE(تاریخ_میلادی)", "min_args": 1, "max_args": 1, "arg_types": ["date"], "return_type": "string",
        "description_fa": "تاریخ میلادی را به فرمت استاندارد شمسی YYYY/MM/DD تبدیل می‌کند.",
        "simple_example": "JALALI_DATE(order_date)",
        "combined_example": "SUBSTRING(JALALI_DATE(order_date), 1, 7)",
        "common_errors_fa": "نیازمند ستون تاریخ معتبر.",
        "engine_notes_fa": "دقیق در سال‌های کبیسه از جمله ۱۴۰۳.",
    },
    # Conditional & Logical
    {
        "id": "CASE", "sql_name": "CASE WHEN ... THEN ... ELSE ... END", "fa_name": "شرط چندگانه", "category": "conditional", "category_fa": "توابع شرطی و منطقی",
        "signature": "CASE WHEN شرط THEN مقدار۱ ELSE مقدار۲ END", "min_args": 1, "max_args": 20, "arg_types": ["any"], "return_type": "same",
        "description_fa": "بر اساس یک یا چند شرط منطقی خروجی متفاوت تولید می‌کند (دسته‌بندی، برچسب‌گذاری، سطوح ریسک).",
        "simple_example": "CASE WHEN amount >= 1000000 THEN 'کلان' ELSE 'عادی' END",
        "combined_example": "SUM(CASE WHEN amount > cost THEN amount - cost ELSE 0 END)",
        "common_errors_fa": "فراموش کردن کلیدواژه END یا ناهمسانی نوع داده خروجی THEN و ELSE.",
        "engine_notes_fa": "هم در ستون ردیفی و هم داخل SUM/COUNT قابل ترکیب است.",
    },
    {
        "id": "COALESCE", "sql_name": "COALESCE", "fa_name": "جایگزینی مقدار خالی", "category": "conditional", "category_fa": "توابع شرطی و منطقی",
        "signature": "COALESCE(مقدار_اصلی, مقدار_جایگزین)", "min_args": 2, "max_args": 10, "arg_types": ["any"], "return_type": "same",
        "description_fa": "اولین مقدار غیرخالی (غیر NULL) را برمی‌گرداند تا محاسبات با NULL خراب نشوند.",
        "simple_example": "COALESCE(amount, 0)",
        "combined_example": "COALESCE(SUM(amount), 0) - COALESCE(SUM(cost), 0)",
        "common_errors_fa": "ارسال مقادیر با نوع ناسازگار (مثلاً عدد و متن).",
        "engine_notes_fa": "توصیه می‌شود در تمام محاسبات جمع و تفریق استفاده شود.",
    },
    {
        "id": "NULLIF", "sql_name": "NULLIF", "fa_name": "محافظ تقسیم بر صفر", "category": "conditional", "category_fa": "توابع شرطی و منطقی",
        "signature": "NULLIF(مخرج, 0)", "min_args": 2, "max_args": 2, "arg_types": ["any", "any"], "return_type": "same",
        "description_fa": "اگر مقدار اول برابر با مقدار دوم (مثلاً صفر) باشد NULL برمی‌گرداند تا خطای تقسیم بر صفر رخ ندهد.",
        "simple_example": "amount / NULLIF(quantity, 0)",
        "combined_example": "ROUND((SUM(amount) - SUM(cost)) * 100.0 / NULLIF(SUM(amount), 0), 2)",
        "common_errors_fa": "تقسیم مستقیم بدون NULLIF باعث شکست کل کوئری روی یک ردیف صفر می‌شود.",
        "engine_notes_fa": "استاندارد SQL برای تقسیم ایمن.",
    },
    {
        "id": "IF", "sql_name": "IF", "fa_name": "شرط دوتایی", "category": "conditional", "category_fa": "توابع شرطی و منطقی",
        "signature": "IF(شرط, در_صورت_صحیح, در_صورت_غلط)", "min_args": 3, "max_args": 3, "arg_types": ["boolean", "any", "any"], "return_type": "same",
        "description_fa": "حالت کوتاه‌شده CASE برای دو خروجی مثبت و منفی است.",
        "simple_example": "IF(amount > 0, 'مثبت', 'نامعتبر')",
        "combined_example": "AVG(IF(amount > cost, 1.0, 0.0)) * 100.0",
        "common_errors_fa": "نوع مقدار دوم و سوم باید سازگار باشد.",
        "engine_notes_fa": "در DuckDB پشتیبانی کامل دارد.",
    },
    # Period & Ratio shortcuts
    {
        "id": "PERCENTAGE", "sql_name": "PERCENTAGE", "fa_name": "درصد سهم از کل", "category": "period", "category_fa": "شاخص‌های مقایسه‌ای و دوره‌ای",
        "signature": "PERCENTAGE(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "سهم درصدی هر گروه را نسبت به مجموع کل محاسبه می‌کند.",
        "simple_example": "PERCENTAGE(amount)",
        "combined_example": "ROUND(PERCENTAGE(amount), 2)",
        "common_errors_fa": "استفاده روی متون غیرعددی.",
        "engine_notes_fa": "به کوئری پنجره‌ای SUM OVER () با محافظ NULLIF کامپایل می‌شود.",
    },
    {
        "id": "MOM", "sql_name": "MOM", "fa_name": "تغییر ماه‌به‌ماه (MoM%)", "category": "period", "category_fa": "شاخص‌های مقایسه‌ای و دوره‌ای",
        "signature": "MOM(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "درصد تغییر هر دوره زمانی نسبت به دوره بلافاصله قبل را محاسبه می‌کند.",
        "simple_example": "MOM(amount)",
        "combined_example": "MOM(amount)",
        "common_errors_fa": "نیازمند وجود بعد زمانی مرتب روی محور افقی است.",
        "engine_notes_fa": "از LAG() OVER (ORDER BY ...) با محافظ تقسیم بر صفر استفاده می‌کند.",
    },
    {
        "id": "YOY", "sql_name": "YOY", "fa_name": "رشد سال‌به‌سال (YoY%)", "category": "period", "category_fa": "شاخص‌های مقایسه‌ای و دوره‌ای",
        "signature": "YOY(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "درصد تغییر هر سال را نسبت به سال قبل محاسبه می‌کند.",
        "simple_example": "YOY(amount)",
        "combined_example": "YOY(amount)",
        "common_errors_fa": "در صورت وجود فقط یک سال داده، مقدار NULL تولید می‌شود.",
        "engine_notes_fa": "کامپایل شده با LAG و NULLIF.",
    },
    {
        "id": "RUNNING_TOTAL", "sql_name": "RUNNING_TOTAL", "fa_name": "جمع تجمعی رونده", "category": "period", "category_fa": "شاخص‌های مقایسه‌ای و دوره‌ای",
        "signature": "RUNNING_TOTAL(ستون_عددی)", "min_args": 1, "max_args": 1, "arg_types": ["numeric"], "return_type": "double",
        "description_fa": "مجموع تجمعی مقادیر را از ابتدا تا ردیف جاری در طول محور زمان محاسبه می‌کند.",
        "simple_example": "RUNNING_TOTAL(amount)",
        "combined_example": "RUNNING_TOTAL(amount)",
        "common_errors_fa": "نیازمند ستون مرتب‌سازی روی محور افقی.",
        "engine_notes_fa": "از پنجره UNBOUNDED PRECEDING AND CURRENT ROW استفاده می‌کند.",
    },
]

_FUNC_ARITY_RULES: dict[str, tuple[int, int]] = {
    "sum": (1, 1), "avg": (1, 1), "count": (1, 1), "min": (1, 1), "max": (1, 1),
    "median": (1, 1), "stddev": (1, 1), "stddev_samp": (1, 1), "variance": (1, 1),
    "percentile_cont": (1, 2), "quantile_cont": (2, 2), "corr": (2, 2),
    "skewness": (1, 1), "kurtosis": (1, 1), "arg_max": (2, 2), "arg_min": (2, 2),
    "last_value": (1, 1), "first_value": (1, 1), "any_value": (1, 1),
    "approx_distinct": (1, 1), "approx_count_distinct": (1, 1),
    "abs": (1, 1), "round": (1, 2), "ceil": (1, 1), "ceiling": (1, 1), "floor": (1, 1),
    "power": (2, 2), "sqrt": (1, 1), "mod": (2, 2), "sign": (1, 1),
    "upper": (1, 1), "lower": (1, 1), "trim": (1, 2), "ltrim": (1, 2), "rtrim": (1, 2),
    "length": (1, 1), "char_length": (1, 1), "substring": (2, 3), "substr": (2, 3),
    "replace": (3, 3), "left": (2, 2), "right": (2, 2), "split_part": (3, 3),
    "date_trunc": (2, 2), "timestamp_trunc": (2, 2), "date_part": (2, 2),
    "date_diff": (3, 3), "datediff": (3, 3), "strftime": (2, 2), "time_to_str": (2, 2),
    "coalesce": (1, 20), "nullif": (2, 2), "if": (3, 3), "iif": (3, 3),
    "jalali_year": (1, 1), "jalali_month": (1, 1), "jalali_quarter": (1, 1), "jalali_date": (1, 1),
}


def _normalize_data_type(dtype: str) -> str:
    low = str(dtype or "string").strip().lower()
    if any(k in low for k in ("int", "bigint", "smallint")):
        return "integer"
    if any(k in low for k in ("double", "float", "real", "decimal", "numeric", "number")):
        return "double"
    if "bool" in low:
        return "boolean"
    if "timestamp" in low or "datetime" in low:
        return "datetime"
    if "date" in low:
        return "date"
    return "string"


def _resolve_field_aliases(
    db: Session,
    user: User,
    dataset_ids: list[str],
    exclude_field_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Loads permitted datasets, visible (CLS-allowed) fields, hidden columns,
    existing calculated fields, and semantic metrics for formula validation & compilation.
    """
    if not dataset_ids:
        raise ValueError("حداقل یک منبع داده باید انتخاب شود")

    datasets: list[Dataset] = []
    hidden_by_ds: dict[str, set[str]] = {}
    all_hidden: set[str] = set()
    field_map_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    unqualified_matches: dict[str, list[dict[str, Any]]] = {}
    alias_lookup: dict[str, dict[str, Any]] = {}
    calc_field_defs: dict[str, dict[str, Any]] = {}
    metric_defs: dict[str, dict[str, Any]] = {}

    for idx, dsid in enumerate(dataset_ids):
        ds = (
            db.query(Dataset)
            .filter(Dataset.id == dsid, Dataset.organization_id == user.organization_id)
            .first()
        )
        if not ds:
            raise PermissionError(f"منبع داده '{dsid}' یافت نشد یا متعلق به سازمان شما نیست")

        dec = permission_resolver.resolve(db, user, "dataset", ds.id, "view")
        if not dec.allowed:
            raise PermissionError(f"عدم دسترسی به منبع داده '{ds.name}' ({dec.reason})")

        sec_ctx = build_data_security_context(db, user, ds.id)
        hidden = set(sec_ctx.hidden_columns or set())
        hidden_by_ds[ds.id] = hidden
        all_hidden.update(hidden)
        datasets.append(ds)

        ds_alias = f"t{idx}"
        fields = db.query(DatasetField).filter(DatasetField.dataset_id == ds.id).all()
        for f in fields:
            if f.field_name in hidden:
                continue
            entry = {
                "id": f.id,
                "dataset_id": ds.id,
                "dataset_name": ds.name,
                "warehouse_table": ds.warehouse_table,
                "table_alias": ds_alias,
                "field_name": f.field_name,
                "display_name": f.display_name,
                "data_type": _normalize_data_type(f.data_type),
                "semantic_role": f.semantic_role,
                "is_calculated": bool(f.is_calculated),
                "calculation_type": getattr(f, "calculation_type", "physical"),
                "expression": f.expression,
                "dependent_fields": list(getattr(f, "dependent_fields_json", []) or []),
            }
            field_map_by_key[(ds.id, f.field_name.lower())] = entry
            unqualified_matches.setdefault(f.field_name.lower(), []).append(entry)
            alias_lookup[f"{ds.name}.{f.field_name}".lower()] = entry
            alias_lookup[f"{ds.name}.{f.display_name}".lower()] = entry
            alias_lookup[f"{ds.warehouse_table}.{f.field_name}".lower()] = entry
            alias_lookup[f"{ds.id}.{f.field_name}".lower()] = entry
            alias_lookup[f"{ds_alias}.{f.field_name}".lower()] = entry
            if f.is_calculated and f.expression and f.field_name != exclude_field_name:
                calc_field_defs[f.field_name.lower()] = entry
                calc_field_defs[f"{ds.id}.{f.field_name}".lower()] = entry

        metrics = (
            db.query(SemanticMetric)
            .filter(SemanticMetric.organization_id == user.organization_id, SemanticMetric.dataset_id == ds.id)
            .all()
        )
        for m in metrics:
            mentry = {
                "id": m.id,
                "dataset_id": m.dataset_id,
                "name": m.name,
                "slug": m.slug,
                "formula": m.formula,
                "output_data_type": getattr(m, "output_data_type", "double"),
                "metric_kind": getattr(m, "metric_kind", "analytical_metric"),
                "dependent_fields": list(getattr(m, "dependent_fields_json", []) or []),
                "dependent_metrics": list(getattr(m, "dependent_metrics_json", []) or []),
            }
            metric_defs[m.slug.lower()] = mentry

    return {
        "datasets": datasets,
        "hidden_by_ds": hidden_by_ds,
        "all_hidden": all_hidden,
        "field_map_by_key": field_map_by_key,
        "unqualified_matches": unqualified_matches,
        "alias_lookup": alias_lookup,
        "calc_field_defs": calc_field_defs,
        "metric_defs": metric_defs,
    }


def _check_dependency_cycles(
    root_name: Optional[str],
    initial_refs: list[str],
    calc_field_defs: dict[str, dict[str, Any]],
    metric_defs: dict[str, dict[str, Any]],
) -> None:
    """DFS cycle detector across calculated fields and analytical metrics."""
    visited_stack: list[str] = [root_name.lower()] if root_name else []

    def _dfs(sym: str, depth: int) -> None:
        if depth > 12:
            raise ValueError("عمق وابستگی فیلدهای محاسباتی از حد مجاز (۱۲) بیشتر است")
        low = sym.lower()
        if low in visited_stack:
            cycle_path = " -> ".join(visited_stack + [low])
            raise ValueError(f"وابستگی چرخه‌ای (Circular Dependency) شناسایی شد: {cycle_path}")
        expr_str = None
        if low in calc_field_defs:
            expr_str = calc_field_defs[low].get("expression")
        elif low in metric_defs:
            expr_str = metric_defs[low].get("formula")
        if not expr_str:
            return
        visited_stack.append(low)
        try:
            try:
                clean_expr = re.sub(r"COUNT\s+DISTINCT", "COUNT_DISTINCT", expr_str, flags=re.IGNORECASE)
                tree = sqlglot.parse_one(f"SELECT {clean_expr} AS _m", read="duckdb")
                for c in tree.find_all(exp.Column):
                    cname = (c.name or "").strip().lower()
                    if cname and (cname in visited_stack or cname in calc_field_defs or cname in metric_defs):
                        _dfs(cname, depth + 1)
            except ValueError:
                raise
            except Exception:
                pass
        finally:
            visited_stack.pop()

    for r in initial_refs:
        _dfs(r, 1)


_AGG_EXP_TYPES = tuple(
    c for c in (
        exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max, exp.Median, exp.Stddev, exp.Variance,
        getattr(exp, "StddevSamp", None), getattr(exp, "StddevPop", None),
        getattr(exp, "VarPop", None), getattr(exp, "PercentileCont", None),
        getattr(exp, "Corr", None), getattr(exp, "Skewness", None),
        getattr(exp, "Kurtosis", None), getattr(exp, "ArgMax", None),
        getattr(exp, "ArgMin", None), getattr(exp, "ApproxDistinct", None),
        getattr(exp, "AnyValue", None),
    ) if c is not None
)


def _rewrite_formula_ast(
    formula: str,
    context: dict[str, Any],
    *,
    qualify_with_alias: bool = True,
    auto_safe_divide: bool = True,
    self_field_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Validates syntax, AST allowlist, CLS permissions, argument arity, data types,
    division-by-zero protection, dependency cycles, and expands nested calculated fields.
    Returns validation report + compiled DuckDB SQL expression.
    """
    raw = (formula or "").strip()
    if not raw:
        raise ValueError("فرمول نمی‌تواند خالی باشد")
    if len(raw) > 4000:
        raise ValueError("طول فرمول از حد مجاز (۴۰۰۰ کاراکتر) بیشتر است")

    # Normalize Persian/Arabic COUNT DISTINCT & whitespace
    normalized = re.sub(r"\bCOUNT\s+DISTINCT\s*\(", "COUNT(DISTINCT ", raw, flags=re.IGNORECASE)

    # Check if shorthand single-function period macro (YOY/MOM/YTD/MTD/PERCENTAGE/RUNNING_TOTAL)
    period_macro = re.match(
        r"^(YOY|MOM|YTD|MTD|PERCENTAGE|RUNNING_TOTAL)\(\s*([A-Za-z0-9_.\"]+)\s*\)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if period_macro:
        fn_macro = period_macro.group(1).upper()
        arg_sym = period_macro.group(2).strip('"')
        # Resolve field existence & CLS before compiling macro
        resolved = _lookup_symbol(arg_sym, context)
        if resolved is None:
            raise ValueError(f"فیلد '{arg_sym}' در منابع انتخاب‌شده یافت نشد یا قابل دسترسی نیست")
        if resolved["data_type"] not in NUMERIC_TYPES:
            raise ValueError(f"تابع {fn_macro} فقط روی فیلدهای عددی قابل استفاده است (نوع '{arg_sym}': {resolved['data_type']})")
        return {
            "valid": True,
            "normalized_formula": normalized,
            "compiled_sql": normalized,
            "is_aggregate": True,
            "is_window_macro": True,
            "macro_name": fn_macro,
            "macro_field": resolved["field_name"],
            "output_data_type": "double",
            "referenced_fields": [resolved],
            "referenced_datasets": [resolved["dataset_id"]],
            "warnings": [],
            "errors": [],
        }

    # Parse into AST
    try:
        parsed_list = sqlglot.parse(f"SELECT {normalized} AS _m", read="duckdb")
    except Exception as exc:
        raise ValueError(f"خطای نحوی فرمول SQL: {exc}") from exc

    statements = [s for s in parsed_list if s is not None]
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("فرمول باید دقیقاً یک عبارت معتبر SQL باشد")

    # Run strict AST allowlist BEFORE column checks
    validate_formula_ast(normalized)

    tree = statements[0]
    warnings: list[str] = []
    referenced_fields: list[dict[str, Any]] = []
    seen_field_keys: set[tuple[str, str]] = set()
    used_datasets: list[str] = []
    used_dataset_set: set[str] = set()
    has_aggregate = False
    has_window = False
    div_by_zero_warnings: list[str] = []

    calc_defs = context["calc_field_defs"]
    metric_defs = context["metric_defs"]
    raw_dep_syms: list[str] = []

    # 1. Inspect AST nodes for arity, aggregates, division-by-zero, and column references
    for node in tree.walk():
        if isinstance(node, exp.Window):
            has_window = True
        if isinstance(node, _AGG_EXP_TYPES):
            has_aggregate = True
        if isinstance(node, exp.Anonymous):
            aname = (node.name or "").strip().lower()
            if aname in AGGREGATE_FUNCS:
                has_aggregate = True
            if aname in _FUNC_ARITY_RULES:
                lo, hi = _FUNC_ARITY_RULES[aname]
                if not (lo <= len(node.expressions) <= hi):
                    raise ValueError(f"تعداد پارامترهای تابع {aname.upper()} باید بین {lo} تا {hi} باشد")
        elif isinstance(node, exp.Func):
            fname = _node_func_name(node)
            if fname in AGGREGATE_FUNCS:
                has_aggregate = True
            if fname in _FUNC_ARITY_RULES:
                # Inspect positional expression children cleanly
                arg_cnt = len(getattr(node, "expressions", []) or []) + (1 if node.this is not None else 0) + (
                    1 if (getattr(node, "expression", None) is not None and not getattr(node, "expressions", None)) else 0
                )
                if isinstance(node, (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max, exp.Median, exp.Abs, exp.Ceil, exp.Floor, exp.Sqrt, exp.Sign, exp.Upper, exp.Lower, exp.Length)):
                    arg_cnt = 1 if node.this is not None else 0
                elif isinstance(node, exp.If):
                    if isinstance(node.parent, exp.Case):
                        continue
                    arg_cnt = (
                        (1 if node.this is not None else 0)
                        + (1 if node.args.get("true") is not None else 0)
                        + (1 if node.args.get("false") is not None else 0)
                    )
                elif isinstance(node, (exp.Nullif, exp.Pow)):
                    arg_cnt = (1 if node.this is not None else 0) + (1 if node.expression is not None else 0)
                lo, hi = _FUNC_ARITY_RULES[fname]
                if arg_cnt < lo or arg_cnt > hi:
                    raise ValueError(f"تعداد آرگومان‌های تابع {fname.upper()} نامعتبر است (مورد انتظار: {lo} تا {hi})")

        if isinstance(node, exp.Div):
            denom = node.expression
            if isinstance(denom, exp.Literal) and not denom.is_string:
                try:
                    if float(denom.this) == 0.0:
                        raise ValueError("تقسیم صریح بر صفر (0) مجاز نیست؛ از NULLIF(..., 0) استفاده کنید")
                except ValueError as ve:
                    if "تقسیم صریح" in str(ve):
                        raise
            elif not isinstance(denom, exp.Nullif):
                div_by_zero_warnings.append(
                    "مخرج تقسیم بدون NULLIF(..., 0) است؛ برای پیشگیری از خطای تقسیم بر صفر به صورت خودکار با NULLIF محافظت شد."
                )

    if div_by_zero_warnings:
        warnings.append(div_by_zero_warnings[0])

    # 2. Check all column references against visible fields + CLS hidden fields
    for col in tree.find_all(exp.Column):
        cname = (col.name or "").strip()
        tname = (col.table or "").strip() or None
        if not cname:
            continue
        sym = f"{tname}.{cname}" if tname else cname
        if self_field_name and cname.lower() == self_field_name.lower():
            raise ValueError(f"وابستگی مستقیم چرخه‌ای به خود فیلد '{self_field_name}' مجاز نیست")

        # Check hidden columns explicitly (CLS)
        if tname:
            # Resolve dataset by name or id or warehouse table
            matched_ds = next(
                (
                    d for d in context["datasets"]
                    if d.name.lower() == tname.lower() or d.id.lower() == tname.lower() or d.warehouse_table.lower() == tname.lower()
                ),
                None,
            )
            if matched_ds and cname in context["hidden_by_ds"].get(matched_ds.id, set()):
                raise PermissionError(f"ستون '{cname}' به دلیل سیاست سطح ستون (CLS) غیرقابل دسترسی است")
        elif cname in context["all_hidden"] and cname.lower() not in context["unqualified_matches"]:
            raise PermissionError(f"ستون '{cname}' به دلیل سیاست سطح ستون (CLS) غیرقابل دسترسی است")

        entry = _lookup_symbol(sym, context)
        if entry is None:
            # Check if it's a metric slug
            if cname.lower() in metric_defs and not tname:
                raw_dep_syms.append(cname.lower())
                continue
            ambiguous = context["unqualified_matches"].get(cname.lower(), [])
            if len(ambiguous) > 1 and not tname:
                ds_names = "، ".join(a["dataset_name"] for a in ambiguous)
                raise ValueError(
                    f"فیلد '{cname}' در چند منبع داده ({ds_names}) وجود دارد؛ لطفاً به صورت نام‌جدول.{cname} بنویسید"
                )
            raise ValueError(f"فیلد یا شاخص '{sym}' در منابع انتخاب‌شده وجود ندارد یا دسترسی به آن محدود است")

        fkey = (entry["dataset_id"], entry["field_name"])
        if fkey not in seen_field_keys:
            seen_field_keys.add(fkey)
            referenced_fields.append(entry)
        if entry["dataset_id"] not in used_dataset_set:
            used_dataset_set.add(entry["dataset_id"])
            used_datasets.append(entry["dataset_id"])
        if entry["is_calculated"]:
            raw_dep_syms.append(entry["field_name"].lower())

    # 3. Dependency Cycle Detection
    _check_dependency_cycles(self_field_name, raw_dep_syms, calc_defs, metric_defs)

    # 4. Data-type checks on functions
    _validate_function_arg_types(tree, context)

    # 5. Transform AST cleanly into DuckDB SQL (expand nested calculated fields, auto NULLIF on division, Jalali expansion)
    def _compile_node(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Div) and auto_safe_divide and not isinstance(node.expression, exp.Nullif):
            if not (isinstance(node.expression, exp.Literal) and not node.expression.is_string):
                safe_denom = exp.Nullif(this=node.expression.copy(), expression=exp.Literal.number(0))
                return exp.Div(this=node.this, expression=safe_denom)
        if isinstance(node, exp.Column):
            cname = (node.name or "").strip()
            tname = (node.table or "").strip() or None
            sym = f"{tname}.{cname}" if tname else cname
            if not tname and cname.lower() in metric_defs:
                m_formula = metric_defs[cname.lower()]["formula"]
                sub = _rewrite_formula_ast(
                    m_formula, context, qualify_with_alias=qualify_with_alias, auto_safe_divide=auto_safe_divide
                )
                return sqlglot.parse_one(f"({sub['compiled_sql']})", read="duckdb")
            entry = _lookup_symbol(sym, context)
            if entry is not None:
                if entry["is_calculated"] and entry.get("expression"):
                    sub = _rewrite_formula_ast(
                        entry["expression"],
                        context,
                        qualify_with_alias=qualify_with_alias,
                        auto_safe_divide=auto_safe_divide,
                        self_field_name=entry["field_name"],
                    )
                    return sqlglot.parse_one(f"({sub['compiled_sql']})", read="duckdb")
                if qualify_with_alias:
                    return exp.Column(
                        this=exp.Identifier(this=entry["field_name"], quoted=True),
                        table=exp.Identifier(this=entry["table_alias"], quoted=False),
                    )
                return exp.Column(this=exp.Identifier(this=entry["field_name"], quoted=True))
        return node

    compiled_tree = tree.transform(_compile_node)
    compiled_inner = compiled_tree.expressions[0].this if isinstance(compiled_tree.expressions[0], exp.Alias) else compiled_tree.expressions[0]
    compiled_sql = compiled_inner.sql(dialect="duckdb")
    compiled_sql = expand_jalali_sql_calls(compiled_sql)
    # Re-validate the final expanded SQL AST for ironclad safety!
    validate_formula_ast(compiled_sql)

    out_type = _infer_output_type(tree, context)
    return {
        "valid": True,
        "normalized_formula": normalized,
        "compiled_sql": compiled_sql,
        "is_aggregate": has_aggregate,
        "is_window_macro": has_window,
        "output_data_type": out_type,
        "referenced_fields": referenced_fields,
        "referenced_datasets": used_datasets,
        "warnings": warnings,
        "errors": [],
    }


def _lookup_symbol(sym: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
    low = sym.strip().strip('"').lower()
    if low in context["alias_lookup"]:
        return context["alias_lookup"][low]
    matches = context["unqualified_matches"].get(low, [])
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_expr_type(node: Optional[exp.Expression], context: dict[str, Any]) -> str:
    if node is None:
        return "unknown"
    if isinstance(node, exp.Literal):
        return "string" if node.is_string else "double"
    if isinstance(node, exp.Column):
        cname = (node.name or "").strip()
        tname = (node.table or "").strip() or None
        entry = _lookup_symbol(f"{tname}.{cname}" if tname else cname, context)
        return entry["data_type"] if entry else "unknown"
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv, exp.Mod, exp.Pow, exp.Abs, exp.Round, exp.Ceil, exp.Floor, exp.Sqrt, exp.Sign, exp.Sum, exp.Avg, exp.Median, exp.Stddev, exp.Variance, exp.Length)):
        return "double"
    if isinstance(node, (exp.Count, exp.Extract)):
        return "integer"
    if isinstance(node, (exp.Upper, exp.Lower, exp.Trim, exp.Concat, exp.Substring, exp.Replace, exp.Left, exp.Right, exp.TimeToStr)):
        return "string"
    if isinstance(node, (exp.CurrentDate, exp.CurrentTimestamp, exp.TimestampTrunc)):
        return "date"
    if isinstance(node, exp.Cast):
        to_t = str(node.to or "").lower()
        return _normalize_data_type(to_t)
    if isinstance(node, exp.Paren):
        return _resolve_expr_type(node.this, context)
    if isinstance(node, exp.Anonymous):
        fn = (node.name or "").strip().lower()
        if fn in ("jalali_year", "jalali_month", "jalali_quarter"):
            return "integer"
        if fn == "jalali_date":
            return "string"
        if fn == "date_part":
            return "integer"
    return "unknown"


def _validate_function_arg_types(tree: exp.Expression, context: dict[str, Any]) -> None:
    for node in tree.walk():
        if isinstance(node, (exp.Sum, exp.Avg, exp.Median, exp.Stddev, exp.Variance, exp.Abs, exp.Sqrt, exp.Ceil, exp.Floor, exp.Sign)):
            t = _resolve_expr_type(node.this, context)
            if t not in ("unknown", *NUMERIC_TYPES):
                fname = (node.sql_name() or type(node).__name__).upper()
                raise ValueError(f"تابع {fname} فقط روی مقادیر عددی کار می‌کند (نوع ورودی: {t})")
        elif isinstance(node, (exp.Upper, exp.Lower, exp.Length, exp.Left, exp.Right, exp.Replace, exp.Substring)):
            target = node.this
            t = _resolve_expr_type(target, context)
            if t not in ("unknown", *STRING_TYPES):
                fname = (node.sql_name() or type(node).__name__).upper()
                raise ValueError(f"تابع متنی {fname} نیازمند ورودی متنی (یا CAST به VARCHAR) است (نوع ورودی: {t})")
        elif isinstance(node, (exp.Extract, exp.TimestampTrunc, exp.TimeToStr)):
            target = node.expression if isinstance(node, (exp.Extract, exp.TimestampTrunc)) else node.this
            t = _resolve_expr_type(target, context)
            if t not in ("unknown", *DATE_TYPES, *STRING_TYPES):
                fname = (node.sql_name() or type(node).__name__).upper()
                raise ValueError(f"تابع تاریخ {fname} روی نوع عددی '{t}' معتبر نیست")


def _infer_output_type(tree: exp.Select, context: dict[str, Any]) -> str:
    expr = tree.expressions[0]
    inner = expr.this if isinstance(expr, exp.Alias) else expr
    inferred = _resolve_expr_type(inner, context)
    return "double" if inferred == "unknown" else inferred


def validate_and_compile_formula(
    db: Session,
    user: User,
    *,
    dataset_ids: list[str],
    formula: str,
    calculation_type: str = "calculated_column",
    qualify_with_alias: bool = True,
    auto_safe_divide: bool = True,
    self_field_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Central entry point used by Validate API, Preview API, Formula Builder, and Query Compiler.
    Enforces:
    - Per-dataset RBAC + CLS hidden-column prevention
    - Strict sqlglot AST allowlist + argument count + type compatibility
    - Calculated Column (row-level) vs Calculated Measure / Analytical Metric (aggregated) semantics
    - Circular dependency detection
    - Division-by-zero safety (`NULLIF`) & decimal precision protection
    """
    ctx = _resolve_field_aliases(db, user, dataset_ids, exclude_field_name=self_field_name)
    res = _rewrite_formula_ast(
        formula,
        ctx,
        qualify_with_alias=qualify_with_alias,
        auto_safe_divide=auto_safe_divide,
        self_field_name=self_field_name,
    )

    calc_kind = (calculation_type or "calculated_column").lower()
    if calc_kind == "calculated_column" and res["is_aggregate"] and not res["is_window_macro"]:
        raise ValueError(
            "ستون محاسباتی (Calculated Column) در سطح ردیف اجرا می‌شود و نمی‌تواند از توابع تجمیعی مثل SUM/AVG استفاده کند؛ نوع را به «شاخص محاسباتی (Calculated Measure)» تغییر دهید."
        )
    if calc_kind in ("calculated_measure", "analytical_metric") and not res["is_aggregate"] and not res["is_window_macro"]:
        res["warnings"].append(
            "این شاخص تجمیعی بدون تابع تجمیعی (مثل SUM یا AVG) نوشته شده است؛ هنگام گروه‌بندی در نمودار ممکن است نیاز به تابع تجمیع داشته باشد."
        )

    return res
