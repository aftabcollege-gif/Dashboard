import readXlsxFile from "read-excel-file/universal";

export interface ParsedExcelColumn {
  field_name: string;
  original_name: string;
  display_name: string;
  data_type: "integer" | "float" | "boolean" | "date" | "string";
  semantic_role: "measure" | "dimension" | "time";
  nullable: boolean;
}

export interface ParsedExcelResult {
  sheets: string[];
  selected_sheet: string;
  total_rows: number;
  duplicate_count: number;
  columns: ParsedExcelColumn[];
  preview_rows: Record<string, any>[];
  all_rows: Record<string, any>[];
}

function normalizeFaDigits(val: string): string {
  return val
    .replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)))
    .replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d)))
    .replace(/ي/g, "ی")
    .replace(/ى/g, "ی")
    .replace(/ك/g, "ک");
}

function tryParseNumber(val: any): number | null {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val !== "string") return null;
  const cleaned = normalizeFaDigits(val).trim().replace(/[,،\s]/g, "");
  if (!cleaned || cleaned === "-" || cleaned === ".") return null;
  if (/^-?\d+(\.\d+)?$/.test(cleaned)) {
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function isDateLike(val: any): boolean {
  if (val instanceof Date && !Number.isNaN(val.getTime())) return true;
  if (typeof val !== "string") return false;
  const s = normalizeFaDigits(val).trim();
  return /^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(s) || /^\d{1,2}[-/]\d{1,2}[-/]\d{4}/.test(s);
}

function inferType(values: any[], overrideType?: string): "integer" | "float" | "boolean" | "date" | "string" {
  if (overrideType && ["integer", "float", "boolean", "date", "string"].includes(overrideType)) {
    return overrideType as any;
  }
  const nonNull = values.filter((v) => v !== null && v !== undefined && String(v).trim() !== "");
  if (nonNull.length === 0) return "string";

  if (nonNull.every((v) => typeof v === "boolean" || ["true", "false"].includes(String(v).trim().toLowerCase()))) {
    return "boolean";
  }
  let intCount = 0;
  let numCount = 0;
  let dateCount = 0;
  for (const v of nonNull.slice(0, 100)) {
    const num = tryParseNumber(v);
    if (num !== null) {
      numCount++;
      if (Number.isInteger(num)) intCount++;
    } else if (isDateLike(v)) {
      dateCount++;
    }
  }
  const sampleLen = Math.min(nonNull.length, 100);
  if (numCount >= sampleLen * 0.85) {
    return intCount === numCount ? "integer" : "float";
  }
  if (dateCount >= sampleLen * 0.8) {
    return "date";
  }
  return "string";
}

function inferSemanticRole(colLabel: string, dtype: string): "measure" | "dimension" | "time" {
  const low = normalizeFaDigits(colLabel).toLowerCase();
  if (dtype === "date") return "time";
  if (["year", "month", "quarter", "day", "date", "سال", "ماه", "فصل", "تاریخ"].some((k) => low === k || low.includes("تاریخ"))) {
    return "time";
  }
  if ((dtype === "integer" || dtype === "float") && !["کد", "شناسه", "id", "code", "شماره", "سال"].some((k) => low === k || low.endsWith("_id"))) {
    return "measure";
  }
  return "dimension";
}

function makeUniqueFieldName(rawHeader: string, idx: number, used: Set<string>): string {
  const ascii = rawHeader
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  let base = ascii && ascii.length >= 2 ? ascii : `col_${idx + 1}`;
  if (/^\d/.test(base)) base = `col_${base}`;
  let candidate = base;
  let n = 2;
  while (used.has(candidate)) {
    candidate = `${base}_${n++}`;
  }
  used.add(candidate);
  return candidate;
}

function parseDelimitedText(text: string, forcedDelimiter?: string): string[][] {
  const cleaned = text.replace(/^\uFEFF/, "");
  const firstLine = cleaned.split(/\r?\n/, 1)[0] || "";
  const delimiter =
    forcedDelimiter ||
    (firstLine.includes("\t")
      ? "\t"
      : firstLine.split(";").length > firstLine.split(",").length
      ? ";"
      : ",");

  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < cleaned.length; i++) {
    const ch = cleaned[i];
    if (inQuotes) {
      if (ch === '"') {
        if (cleaned[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\r") {
      if (cleaned[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

export async function parseSpreadsheetBuffer(
  buffer: ArrayBuffer | Uint8Array,
  filename: string,
  options: {
    sheetName?: string;
    hasHeader?: boolean;
    nullStrategy?: string;
    deduplicate?: boolean;
    columnRenames?: Record<string, string>;
    typeOverrides?: Record<string, string>;
  } = {}
): Promise<ParsedExcelResult> {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  const hasHeader = options.hasHeader !== false;
  const nullStrategy = options.nullStrategy || "keep";
  const deduplicate = Boolean(options.deduplicate);
  const columnRenames = options.columnRenames || {};
  const typeOverrides = options.typeOverrides || {};

  let rawMatrix: any[][] = [];
  let sheets: string[] = [];
  let selectedSheet = options.sheetName || "";

  if (ext === "json") {
    const text = new TextDecoder("utf-8").decode(buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer));
    const parsed = JSON.parse(text);
    const arr = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.data) ? parsed.data : [parsed];
    if (arr.length > 0 && typeof arr[0] === "object") {
      const keys = Array.from(new Set<string>(arr.flatMap((r: any) => Object.keys(r || {}))));
      rawMatrix = [keys, ...arr.map((r: any) => keys.map((k) => r?.[k] ?? ""))];
    }
    sheets = ["JSON"];
    selectedSheet = "JSON";
  } else if (ext === "csv" || ext === "tsv" || ext === "txt") {
    const text = new TextDecoder("utf-8").decode(buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer));
    rawMatrix = parseDelimitedText(text, ext === "tsv" ? "\t" : undefined);
    sheets = [ext.toUpperCase()];
    selectedSheet = ext.toUpperCase();
  } else if (ext === "xls") {
    throw new Error(
      "Legacy binary .xls files require server-side upload or saving as .xlsx/.csv in browser preview mode."
    );
  } else {
    const inputBuffer = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    const parsedSheets = await readXlsxFile(inputBuffer);
    sheets = parsedSheets.map((s) => s.sheet);
    const targetSheet =
      (selectedSheet ? parsedSheets.find((s) => s.sheet === selectedSheet) : undefined) || parsedSheets[0];
    selectedSheet = targetSheet?.sheet || "";
    rawMatrix = (targetSheet?.data || []).map((row) =>
      row.map((cell) => (cell === null || cell === undefined ? "" : cell))
    );
  }

  // Filter completely empty trailing/leading rows
  rawMatrix = rawMatrix.filter((row) => Array.isArray(row) && row.some((cell) => String(cell ?? "").trim() !== ""));

  if (rawMatrix.length === 0) {
    return {
      sheets,
      selected_sheet: selectedSheet,
      total_rows: 0,
      duplicate_count: 0,
      columns: [],
      preview_rows: [],
      all_rows: [],
    };
  }

  const maxCols = rawMatrix.reduce((mx, r) => Math.max(mx, r.length), 0);
  const headerRow = hasHeader
    ? Array.from({ length: maxCols }, (_, i) => String(rawMatrix[0][i] ?? "").trim() || `ستون ${i + 1}`)
    : Array.from({ length: maxCols }, (_, i) => `ستون ${i + 1}`);
  const dataMatrix = hasHeader ? rawMatrix.slice(1) : rawMatrix;

  const usedNames = new Set<string>();
  const colDefs = headerRow.map((rawHdr, idx) => {
    const displayBase = columnRenames[rawHdr] || columnRenames[`col_${idx + 1}`] || rawHdr;
    const fieldName = makeUniqueFieldName(displayBase, idx, usedNames);
    const finalDisplay = columnRenames[fieldName] || displayBase;
    return { idx, rawHdr, fieldName, displayName: finalDisplay };
  });

  // Build raw records
  let records: Record<string, any>[] = dataMatrix.map((row) => {
    const obj: Record<string, any> = {};
    for (const c of colDefs) {
      let val = row[c.idx];
      if (val instanceof Date && !Number.isNaN(val.getTime())) {
        val = val.toISOString().slice(0, 10);
      } else if (typeof val === "string") {
        val = val.trim();
      }
      obj[c.fieldName] = val === undefined || val === null ? "" : val;
    }
    return obj;
  });

  // Null strategy
  if (nullStrategy === "drop_rows") {
    records = records.filter((r) => colDefs.every((c) => r[c.fieldName] !== "" && r[c.fieldName] !== null && r[c.fieldName] !== undefined));
  }

  // Duplicate detection & optional deduplication
  const seen = new Set<string>();
  let duplicateCount = 0;
  const deduped: Record<string, any>[] = [];
  for (const r of records) {
    const sig = JSON.stringify(r);
    if (seen.has(sig)) {
      duplicateCount++;
      if (!deduplicate) deduped.push(r);
    } else {
      seen.add(sig);
      deduped.push(r);
    }
  }
  records = deduped;

  // Infer column types and cast values cleanly
  const columns: ParsedExcelColumn[] = colDefs.map((c) => {
    const vals = records.map((r) => r[c.fieldName]);
    const ov = typeOverrides[c.fieldName] || typeOverrides[c.rawHdr];
    const dtype = inferType(vals, ov);
    const nullable = vals.some((v) => v === "" || v === null || v === undefined);
    const semanticRole = inferSemanticRole(c.displayName, dtype);
    return {
      field_name: c.fieldName,
      original_name: c.rawHdr,
      display_name: c.displayName,
      data_type: dtype,
      semantic_role: semanticRole,
      nullable,
    };
  });

  // Compute numeric means for fill_mean if selected
  const means: Record<string, number> = {};
  if (nullStrategy === "fill_mean") {
    for (const col of columns) {
      if (col.data_type === "integer" || col.data_type === "float") {
        const nums = records.map((r) => tryParseNumber(r[col.field_name])).filter((x): x is number => x !== null);
        means[col.field_name] = nums.length ? Math.round((nums.reduce((a, b) => a + b, 0) / nums.length) * 100) / 100 : 0;
      }
    }
  }

  const typedRows = records.map((r) => {
    const out: Record<string, any> = {};
    for (const col of columns) {
      const rawVal = r[col.field_name];
      const isEmpty = rawVal === "" || rawVal === null || rawVal === undefined;
      if (col.data_type === "integer" || col.data_type === "float") {
        if (isEmpty) {
          out[col.field_name] =
            nullStrategy === "fill_zero" ? 0 : nullStrategy === "fill_mean" ? means[col.field_name] ?? 0 : null;
        } else {
          const parsed = tryParseNumber(rawVal);
          out[col.field_name] =
            parsed !== null ? (col.data_type === "integer" ? Math.round(parsed) : parsed) : 0;
        }
      } else if (col.data_type === "boolean") {
        if (isEmpty) out[col.field_name] = false;
        else out[col.field_name] = String(rawVal).toLowerCase() === "true" || rawVal === true || rawVal === 1;
      } else {
        if (isEmpty) {
          out[col.field_name] = nullStrategy === "fill_zero" ? "نامشخص" : "";
        } else {
          out[col.field_name] = String(rawVal);
        }
      }
    }
    return out;
  });

  return {
    sheets,
    selected_sheet: selectedSheet,
    total_rows: typedRows.length,
    duplicate_count: duplicateCount,
    columns,
    preview_rows: typedRows.slice(0, 50),
    all_rows: typedRows,
  };
}
