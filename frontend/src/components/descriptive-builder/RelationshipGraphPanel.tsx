"use client";

import React, { useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  GitBranch,
  Link2,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Unlink,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import type {
  AccessibleDatasetMeta,
  JoinSuggestion,
  RelationshipAnalyzeResponse,
} from "@/types/descriptiveBuilder";

interface Props {
  isFa: boolean;
  selectedDatasets: AccessibleDatasetMeta[];
  analysis: RelationshipAnalyzeResponse | null;
  analyzing: boolean;
  primaryDatasetId: string;
  onPrimaryDatasetChange: (id: string) => void;
  chosenRelIds: string[];
  onChosenRelIdsChange: (ids: string[]) => void;
  adHocJoins: Record<string, any>[];
  onAdHocJoinsChange: (joins: Record<string, any>[]) => void;
  onReanalyze: () => void;
  showToast: (msg: string) => void;
}

export function RelationshipGraphPanel({
  isFa,
  selectedDatasets,
  analysis,
  analyzing,
  primaryDatasetId,
  onPrimaryDatasetChange,
  chosenRelIds,
  onChosenRelIdsChange,
  adHocJoins,
  onAdHocJoinsChange,
  onReanalyze,
  showToast,
}: Props) {
  const [leftDsId, setLeftDsId] = useState(selectedDatasets[0]?.id || "");
  const [leftField, setLeftField] = useState("");
  const [rightDsId, setRightDsId] = useState(selectedDatasets[1]?.id || "");
  const [rightField, setRightField] = useState("");
  const [joinType, setJoinType] = useState("left");
  const [cardinality, setCardinality] = useState("many_to_one");
  const [savingRel, setSavingRel] = useState(false);

  const leftDs = useMemo(
    () => selectedDatasets.find((d) => d.id === (leftDsId || selectedDatasets[0]?.id)),
    [selectedDatasets, leftDsId]
  );
  const rightDs = useMemo(
    () => selectedDatasets.find((d) => d.id === (rightDsId || selectedDatasets[1]?.id)),
    [selectedDatasets, rightDsId]
  );

  const handleSavePersistentRelationship = async (
    lDsId: string,
    lField: string,
    rDsId: string,
    rField: string,
    jType: string,
    card: string
  ) => {
    if (!lDsId || !lField || !rDsId || !rField || lDsId === rDsId) {
      showToast(isFa ? "لطفاً دو دیتاست متفاوت و ستون کلید هر دو را انتخاب کنید." : "Select two distinct datasets and their key fields.");
      return;
    }
    setSavingRel(true);
    try {
      await apiFetch("/descriptive-builder/relationships", {
        method: "POST",
        body: JSON.stringify({
          left_dataset_id: lDsId,
          left_field: lField,
          right_dataset_id: rDsId,
          right_field: rField,
          join_type: jType,
          cardinality: card,
        }),
      });
      showToast(isFa ? "رابطه جدید با موفقیت در لایه مدل داده ذخیره شد." : "Relationship saved in data model.");
      onReanalyze();
    } catch (err: any) {
      showToast(err.message || (isFa ? "خطا در ثبت رابطه" : "Failed to save relationship"));
    } finally {
      setSavingRel(false);
    }
  };

  const handleAddAdHocJoin = (sug: JoinSuggestion) => {
    const exists = adHocJoins.some(
      (j) =>
        (j.left_dataset_id === sug.left_dataset_id &&
          j.right_dataset_id === sug.right_dataset_id &&
          j.left_field === sug.left_field &&
          j.right_field === sug.right_field) ||
        (j.left_dataset_id === sug.right_dataset_id &&
          j.right_dataset_id === sug.left_dataset_id)
    );
    if (exists) return;
    onAdHocJoinsChange([
      ...adHocJoins,
      {
        left_dataset_id: sug.left_dataset_id,
        left_field: sug.left_field,
        right_dataset_id: sug.right_dataset_id,
        right_field: sug.right_field,
        join_type: sug.suggested_join_type || "left",
        cardinality: sug.suggested_cardinality || "many_to_one",
      },
    ]);
    showToast(isFa ? "اتصال موقت در سطح همین داشبورد اعمال شد." : "Temporary model join applied.");
  };

  if (selectedDatasets.length <= 1) {
    return (
      <div className="bi-card p-5 space-y-2 text-xs">
        <div className="flex items-center gap-2 font-bold text-sm">
          <CheckCircle2 className="w-4 h-4 text-emerald-500" />
          {isFa ? "تحلیل تک‌منبعی بدون نیاز به Join" : "Single-Dataset Model Ready"}
        </div>
        <p style={{ color: "var(--bi-muted)" }}>
          {isFa
            ? "شما یک دیتاست انتخاب کرده‌اید؛ برای مدل‌سازی روابط چندجدولی می‌توانید در مرحله قبل بیش از یک منبع را انتخاب کنید."
            : "You selected one dataset. To model multi-table joins, select 2+ datasets in Step 1."}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4 text-xs">
      {/* Status Banner + Primary Fact Selector */}
      <div className="bi-card p-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          {analysis?.ready_for_join ? (
            <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
          ) : (
            <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0" />
          )}
          <div>
            <div className="font-bold text-sm">
              {analysis?.ready_for_join
                ? isFa
                  ? "گراف روابط چندمنبعی آماده اجراست"
                  : "Multi-Source Join Graph Ready"
                : isFa
                ? "مدل روابط نیازمند تکمیل یا رفع ابهام است"
                : "Join Graph Requires Attention"}
            </div>
            <div className="text-[11px]" style={{ color: "var(--bi-muted)" }}>
              {isFa
                ? "موتور هوشمند هرگز بدون رابطه مشخص، داده‌ها را به‌صورت حدسی Join نمی‌کند."
                : "Strict governance: disconnected or ambiguous tables are never speculatively joined."}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="font-semibold text-[11px]">{isFa ? "جدول مبنا (Fact):" : "Primary Dataset:"}</label>
          <select
            value={primaryDatasetId || analysis?.primary_dataset_id || selectedDatasets[0]?.id || ""}
            onChange={(e) => onPrimaryDatasetChange(e.target.value)}
            className="px-2.5 py-1.5 rounded-lg border text-xs font-medium"
            style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
          >
            {selectedDatasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.row_count.toLocaleString()} ردیف)
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={onReanalyze}
            disabled={analyzing}
            className="px-3 py-1.5 rounded-lg border font-semibold flex items-center gap-1 hover:opacity-80"
            style={{ borderColor: "var(--bi-border)" }}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${analyzing ? "animate-spin" : ""}`} />
            {isFa ? "بازبینی گراف" : "Re-Analyze"}
          </button>
        </div>
      </div>

      {/* Visual Nodes & Resolved Edges Card */}
      <div className="bi-card p-4 space-y-3">
        <div className="font-bold text-xs flex items-center gap-1.5">
          <GitBranch className="w-4 h-4 text-blue-500" />
          {isFa ? "نمای گراف جداول و اتصالات فعال" : "Active Dataset Graph & Resolved Joins"}
        </div>
        <div className="flex flex-wrap gap-2">
          {selectedDatasets.map((ds) => {
            const isPrimary = (primaryDatasetId || analysis?.primary_dataset_id) === ds.id;
            const isDisc = analysis?.disconnected_datasets.some((d) => d.id === ds.id);
            return (
              <div
                key={ds.id}
                className={`px-3 py-2 rounded-xl border flex items-center gap-2 ${
                  isDisc
                    ? "border-amber-500/60 bg-amber-500/10"
                    : isPrimary
                    ? "border-blue-500 bg-blue-500/10"
                    : "border-emerald-500/40 bg-emerald-500/5"
                }`}
              >
                <span className="font-bold">{ds.name}</span>
                {isPrimary && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500 text-white font-semibold">
                    {isFa ? "مبنا" : "Primary"}
                  </span>
                )}
                {isDisc && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500 text-white font-semibold">
                    {isFa ? "قطع‌شده" : "Disconnected"}
                  </span>
                )}
                <span className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                  {ds.fields.length} {isFa ? "ستون" : "cols"}
                </span>
              </div>
            );
          })}
        </div>

        {(analysis?.resolved_joins?.length || 0) > 0 && (
          <div className="space-y-1.5 pt-2 border-t" style={{ borderColor: "var(--bi-border)" }}>
            <div className="text-[11px] font-semibold" style={{ color: "var(--bi-muted)" }}>
              {isFa ? "مسیر Join قطعی (پشتیبانی از اتصال چندگام / Bridge):" : "Resolved Multi-Hop Join Chain:"}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {analysis?.resolved_joins.map((j) => (
                <div
                  key={j.id}
                  className="px-3 py-2 rounded-lg border flex items-center justify-between gap-2"
                  style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}
                >
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Link2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                    <span className="font-semibold">{j.left_dataset_name}</span>
                    <code className="text-[10px] px-1 rounded bg-slate-500/15">{j.left_field}</code>
                    <ArrowRightLeft className="w-3 h-3 text-blue-500" />
                    <span className="font-semibold">{j.right_dataset_name}</span>
                    <code className="text-[10px] px-1 rounded bg-slate-500/15">{j.right_field}</code>
                  </div>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 font-semibold uppercase">
                    {j.join_type} · {j.cardinality}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Ambiguous Paths Resolver */}
      {(analysis?.ambiguous_paths?.length || 0) > 0 && (
        <div className="bi-card p-4 space-y-3 border-amber-500/60 bg-amber-500/5">
          <div className="font-bold text-sm text-amber-600 dark:text-amber-400 flex items-center gap-2">
            <ShieldAlert className="w-4 h-4" />
            {isFa
              ? "مسیرهای چندگانه (ابهام در Join) — لطفاً مسیر موردنظر را انتخاب کنید"
              : "Ambiguous Join Paths Detected — Select Intended Path"}
          </div>
          {analysis?.ambiguous_paths.map((amb) => (
            <div key={`${amb.from_dataset_id}-${amb.to_dataset_id}`} className="space-y-2">
              <div className="font-semibold">
                {amb.from_dataset_name} ←→ {amb.to_dataset_name}
              </div>
              <div className="space-y-1.5">
                {amb.candidate_paths.map((cp) => {
                  const selected = cp.relationship_ids.every((rid) => chosenRelIds.includes(rid));
                  return (
                    <label
                      key={cp.path_key}
                      className={`flex items-center justify-between gap-2 p-2.5 rounded-lg border cursor-pointer transition ${
                        selected ? "border-blue-500 bg-blue-500/10" : ""
                      }`}
                      style={!selected ? { borderColor: "var(--bi-border)" } : undefined}
                    >
                      <div className="flex items-center gap-2">
                        <input
                          type="radio"
                          name={`amb_${amb.to_dataset_id}`}
                          checked={selected}
                          onChange={() => {
                            const next = new Set(chosenRelIds);
                            amb.candidate_paths.forEach((other) =>
                              other.relationship_ids.forEach((rid) => next.delete(rid))
                            );
                            cp.relationship_ids.forEach((rid) => next.add(rid));
                            onChosenRelIdsChange(Array.from(next));
                          }}
                        />
                        <span className="font-mono text-[11px]">{cp.steps.join("  →  ")}</span>
                      </div>
                      <span className="text-[10px]" style={{ color: "var(--bi-muted)" }}>
                        {cp.relationship_ids.length} {isFa ? "گام" : "hop(s)"}
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* M:N Row-Explosion Risk Checks */}
      {(analysis?.explosion_checks?.filter((c) => c.risk !== "low").length || 0) > 0 && (
        <div className="bi-card p-4 space-y-2 border-red-500/50 bg-red-500/5">
          <div className="font-bold text-sm text-red-500 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {isFa ? "هشدار ریسک افزایش ردیف‌ها (Row Explosion / M:N)" : "Join Row-Explosion Warning"}
          </div>
          {analysis?.explosion_checks
            .filter((c) => c.risk !== "low")
            .map((chk) => (
              <div key={chk.relationship_id} className="p-2.5 rounded-lg border border-red-500/30 space-y-1">
                <div className="flex items-center justify-between font-semibold">
                  <span>
                    {chk.left_dataset_name} ⇄ {chk.right_dataset_name} ({chk.join_field})
                  </span>
                  <span className="px-2 py-0.5 rounded bg-red-500 text-white text-[10px]">
                    {chk.expansion_factor}x ({chk.left_rows} → {chk.joined_rows})
                  </span>
                </div>
                <p className="text-[11px] text-red-600 dark:text-red-300">{chk.warning_fa}</p>
              </div>
            ))}
        </div>
      )}

      {/* Disconnected Datasets + Smart Join Suggestions */}
      {(analysis?.disconnected_datasets?.length || 0) > 0 && (
        <div className="bi-card p-4 space-y-3 border-amber-500/50">
          <div className="font-bold text-sm text-amber-500 flex items-center gap-2">
            <Unlink className="w-4 h-4" />
            {isFa
              ? `دیتاست‌های بدون رابطه: ${analysis?.disconnected_datasets.map((d) => d.name).join("، ")}`
              : `Disconnected Datasets: ${analysis?.disconnected_datasets.map((d) => d.name).join(", ")}`}
          </div>

          {(analysis?.suggestions?.length || 0) > 0 && (
            <div className="space-y-2">
              <div className="font-semibold flex items-center gap-1.5 text-violet-500">
                <Sparkles className="w-3.5 h-3.5" />
                {isFa ? "پیشنهاد هوشمند کلید ارتباط (با تأیید شما):" : "Smart Join Key Suggestions:"}
              </div>
              {analysis?.suggestions.map((sug, i) => (
                <div
                  key={`${sug.left_dataset_id}-${sug.left_field}-${sug.right_dataset_id}-${i}`}
                  className="p-2.5 rounded-lg border flex flex-wrap items-center justify-between gap-2"
                  style={{ borderColor: "var(--bi-border)", background: "var(--bi-surface)" }}
                >
                  <div>
                    <span className="font-bold">{sug.left_dataset_name}</span>.
                    <code className="px-1 rounded bg-slate-500/15">{sug.left_field}</code> ={" "}
                    <span className="font-bold">{sug.right_dataset_name}</span>.
                    <code className="px-1 rounded bg-slate-500/15">{sug.right_field}</code>
                    <span className="ms-2 text-[10px] text-emerald-500 font-semibold">
                      ({Math.round(sug.confidence * 100)}% تطابق)
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => handleAddAdHocJoin(sug)}
                      className="px-2.5 py-1 rounded border font-semibold hover:bg-blue-500/10"
                      style={{ borderColor: "var(--bi-border)" }}
                    >
                      {isFa ? "اتصال موقت در مدل" : "Apply Ad-Hoc"}
                    </button>
                    <button
                      type="button"
                      disabled={savingRel}
                      onClick={() =>
                        handleSavePersistentRelationship(
                          sug.left_dataset_id,
                          sug.left_field,
                          sug.right_dataset_id,
                          sug.right_field,
                          sug.suggested_join_type,
                          sug.suggested_cardinality
                        )
                      }
                      className="px-2.5 py-1 rounded text-white font-semibold bg-emerald-600 hover:bg-emerald-700"
                    >
                      {isFa ? "ذخیره دائمی رابطه" : "Save Relationship"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Active Temporary (Ad-Hoc) Joins list */}
      {adHocJoins.length > 0 && (
        <div className="bi-card p-3 flex flex-wrap items-center justify-between gap-2">
          <div className="font-semibold">
            {isFa ? `${adHocJoins.length} اتصال موقت فعال است:` : `${adHocJoins.length} ad-hoc join(s) active:`}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {adHocJoins.map((j, idx) => (
              <span
                key={idx}
                className="px-2 py-1 rounded-lg bg-violet-500/15 text-violet-500 font-semibold flex items-center gap-1.5"
              >
                {j.left_field} = {j.right_field} ({j.join_type})
                <button
                  type="button"
                  onClick={() => onAdHocJoinsChange(adHocJoins.filter((_, i) => i !== idx))}
                  className="text-red-500 font-bold"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Manual Relationship Builder */}
      <div className="bi-card p-4 space-y-3">
        <div className="font-bold flex items-center gap-1.5">
          <Plus className="w-4 h-4 text-blue-500" />
          {isFa ? "تعریف دستی رابطه بین دو دیتاست" : "Create Relationship Manually"}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-2 items-end">
          <div>
            <label className="block text-[10px] font-semibold mb-1">{isFa ? "جدول چپ" : "Left Dataset"}</label>
            <select
              value={leftDs?.id || ""}
              onChange={(e) => {
                setLeftDsId(e.target.value);
                setLeftField("");
              }}
              className="w-full px-2 py-1.5 rounded border"
              style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
            >
              {selectedDatasets.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10px] font-semibold mb-1">{isFa ? "ستون چپ" : "Left Column"}</label>
            <select
              value={leftField}
              onChange={(e) => setLeftField(e.target.value)}
              className="w-full px-2 py-1.5 rounded border"
              style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
            >
              <option value="">-- {isFa ? "انتخاب" : "Select"} --</option>
              {(leftDs?.fields || []).map((f) => (
                <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10px] font-semibold mb-1">{isFa ? "جدول راست" : "Right Dataset"}</label>
            <select
              value={rightDs?.id || ""}
              onChange={(e) => {
                setRightDsId(e.target.value);
                setRightField("");
              }}
              className="w-full px-2 py-1.5 rounded border"
              style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
            >
              {selectedDatasets.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10px] font-semibold mb-1">{isFa ? "ستون راست" : "Right Column"}</label>
            <select
              value={rightField}
              onChange={(e) => setRightField(e.target.value)}
              className="w-full px-2 py-1.5 rounded border"
              style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
            >
              <option value="">-- {isFa ? "انتخاب" : "Select"} --</option>
              {(rightDs?.fields || []).map((f) => (
                <option key={f.field_name} value={f.field_name}>{f.display_name || f.field_name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10px] font-semibold mb-1">{isFa ? "نوع Join" : "Join & Card."}</label>
            <div className="flex gap-1">
              <select
                value={joinType}
                onChange={(e) => setJoinType(e.target.value)}
                className="w-1/2 px-1.5 py-1.5 rounded border"
                style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
              >
                <option value="left">LEFT</option>
                <option value="inner">INNER</option>
                <option value="right">RIGHT</option>
              </select>
              <select
                value={cardinality}
                onChange={(e) => setCardinality(e.target.value)}
                className="w-1/2 px-1.5 py-1.5 rounded border"
                style={{ background: "var(--bi-surface)", borderColor: "var(--bi-border)" }}
              >
                <option value="many_to_one">N:1</option>
                <option value="one_to_one">1:1</option>
                <option value="one_to_many">1:N</option>
                <option value="many_to_many">M:N</option>
              </select>
            </div>
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              disabled={savingRel || !leftDs || !rightDs || !leftField || !rightField}
              onClick={() =>
                leftDs &&
                rightDs &&
                handleSavePersistentRelationship(leftDs.id, leftField, rightDs.id, rightField, joinType, cardinality)
              }
              className="w-full py-1.5 px-2.5 rounded text-white font-semibold disabled:opacity-40"
              style={{ background: "var(--bi-primary)" }}
            >
              {isFa ? "ثبت رابطه" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
