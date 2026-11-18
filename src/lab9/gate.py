"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]

    # 1. Non-finite metric values (nan < min is False, so a naive gate passes them).
    for name, rep in (("PR", pr), ("baseline", base)):
        for k, v in (rep.get("metrics") or {}).items():
            if isinstance(v, float) and not math.isfinite(v):
                problems.append(f"{name} metric {k} is {v} (non-finite)")

    # 2. Crashed graders: listed failures, or a graded value that is missing / not a bool.
    for name, rep in (("PR", pr), ("baseline", base)):
        if rep.get("evaluator_failures"):
            problems.append(f"{name} has {len(rep['evaluator_failures'])} evaluator failure(s)")
        for m in graded:
            bad = [r for r in rep.get("rows", []) if not isinstance(r.get(m), bool)]
            if bad:
                problems.append(f"{name} has {len(bad)} row(s) where {m} is not graded "
                                f"(e.g. {bad[0].get('item_id')}@{bad[0].get('seed')} = {bad[0].get(m)!r})")

    # 3. Row coverage: count, duplicates, and the exact (item, seed) set of the baseline.
    man, bman = pr.get("manifest", {}), base.get("manifest", {})
    rows, brows = pr.get("rows", []), base.get("rows", [])
    want = int(man.get("items") or 0) * len(man.get("seeds") or [])
    if len(rows) != want:
        problems.append(f"PR has {len(rows)} rows, manifest says {man.get('items')} items x "
                        f"{len(man.get('seeds') or [])} seeds = {want}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    bkeys = [(r.get("item_id"), r.get("seed")) for r in brows]
    dups = len(keys) - len(set(keys))
    if dups:
        problems.append(f"PR has {dups} duplicated (item, seed) row(s)")
    if len(bkeys) != len(set(bkeys)):
        problems.append(f"baseline has {len(bkeys) - len(set(bkeys))} duplicated (item, seed) row(s)")
    if man.get("items") != bman.get("items") or list(man.get("seeds") or []) != list(bman.get("seeds") or []):
        problems.append(f"manifest differs from baseline: items {man.get('items')} vs {bman.get('items')}, "
                        f"seeds {man.get('seeds')} vs {bman.get('seeds')}")
    if len({k[0] for k in keys}) != man.get("items"):
        problems.append(f"PR rows cover {len({k[0] for k in keys})} items, manifest says {man.get('items')}")
    missing, extra = set(bkeys) - set(keys), set(keys) - set(bkeys)
    if missing or extra:
        problems.append(f"(item, seed) set differs from baseline: {len(missing)} missing, {len(extra)} extra"
                        + (f" (e.g. missing {sorted(missing, key=str)[0]})" if missing else ""))

    # 4. Reported metrics must be what the rows say (a hand-edited number with intact rows).
    for k, got, exp in _recomputed(pr, spec):
        if got is None or exp is None or abs(float(got) - float(exp)) > 1e-3:
            problems.append(f"PR metric {k}={got} but its rows give {exp}")

    # 5. Slice tags are derived from labels: they may not move between baseline and PR.
    btags = {(r.get("item_id"), r.get("seed")): sorted(r.get("slices") or []) for r in brows}
    moved = sorted({k[0] for k, r in zip(keys, rows) if k in btags and sorted(r.get("slices") or []) != btags[k]})
    if moved:
        problems.append(f"slice tags differ from baseline for {len(moved)} item(s), e.g. {moved[0]}")

    # 6. Ratchet: on the same eval-set version a spec may only get stricter.
    if base_spec is not None:
        problems += _ratchet(spec, base_spec, pr, base)
    return problems


def _recomputed(rep: dict, spec: dict) -> list[tuple[str, object, object]]:
    """(metric, reported, recomputed-from-rows) for every metric the rows can reproduce."""
    rows = rep.get("rows") or []
    if not rows:
        return []
    out = []
    reported = rep.get("metrics") or {}
    for k, got in reported.items():
        if k in GRADERS and all(k in r for r in rows):
            vals = [r[k] for r in rows if isinstance(r.get(k), bool)]
            if k in ("secret_leak", "approval_bypass"):
                exp = sum(vals)
            else:
                exp = round(sum(vals) / len(vals), 4) if vals else None
            if isinstance(got, (int, float)) and not (isinstance(got, float) and not math.isfinite(got)):
                out.append((k, got, exp))
    if all("pass_rate" in r for r in rows):
        by_item: dict[str, list[bool]] = {}
        for r in rows:
            by_item.setdefault(r["item_id"], []).append(bool(r.get("pass_rate")))
        derived = {"pass_5": round(sum(all(v) for v in by_item.values()) / len(by_item), 4)}
        n_min = min(len(v) for v in by_item.values())
        for k in spec.get("pass_k") or []:
            if n_min >= k:
                for name, fn in (("pass_at", stats.pass_at_k), ("pass_hat", stats.pass_hat_k)):
                    try:
                        est = [fn(len(v), sum(v), k) for v in by_item.values()]
                    except (NotImplementedError, ValueError):
                        continue
                    derived[f"{name}_{k}"] = round(sum(est) / len(est), 4)
        for k, exp in derived.items():
            got = reported.get(k)
            if isinstance(got, (int, float)) and math.isfinite(float(got)):
                out.append((k, got, exp))
    return out


_DIRECTION = {"min": -1, "max": +1, "max_increase": +1}   # -1: may not fall, +1: may not rise


def _looser(th: dict, bth: dict, where: str) -> list[str]:
    out = []
    for key, sign in _DIRECTION.items():
        if key in bth:
            if key not in th:
                out.append(f"RATCHET {where}: threshold {key} removed (was {bth[key]})")
            elif (th[key] - bth[key]) * sign > 1e-12:
                out.append(f"RATCHET {where}: {key} {bth[key]} -> {th[key]} is looser")
    if bth.get("no_significant_drop") and not th.get("no_significant_drop"):
        out.append(f"RATCHET {where}: no_significant_drop dropped")
    return out


def _ratchet(spec: dict, base_spec: dict, pr: dict, base: dict) -> list[str]:
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        if pr["manifest"].get("eval_set_sha") == base["manifest"].get("eval_set_sha"):
            return [f"RATCHET eval_set_version {base_spec.get('eval_set_version')} -> "
                    f"{spec.get('eval_set_version')} but the eval set itself is unchanged"]
        return []
    out = []
    cur = {m["id"]: m for m in spec.get("metrics", [])}
    for bm in base_spec.get("metrics", []):
        m = cur.get(bm["id"])
        where = f"metric {bm['id']}"
        if m is None:
            out.append(f"RATCHET {where} removed")
            continue
        if bm.get("blocking") and not m.get("blocking"):
            out.append(f"RATCHET {where} no longer blocking")
        if bm.get("kind") != m.get("kind"):
            out.append(f"RATCHET {where} kind {bm.get('kind')} -> {m.get('kind')}")
        out += _looser(m.get("threshold") or {}, bm.get("threshold") or {}, where)
    cur_s = {(s.get("name"), s.get("metric")): s for s in spec.get("slices") or []}
    for bs in base_spec.get("slices") or []:
        s = cur_s.get((bs.get("name"), bs.get("metric")))
        where = f"slice {bs.get('name')}/{bs.get('metric')}"
        if s is None:
            out.append(f"RATCHET {where} removed")
            continue
        if bs.get("blocking") and not s.get("blocking"):
            out.append(f"RATCHET {where} no longer blocking")
        out += _looser(s, bs, where)
    for key in ("seeds", "eval_set", "labels", "adversarial_pack", "pass_k"):
        if base_spec.get(key) != spec.get(key):
            out.append(f"RATCHET {key} changed on the same eval-set version: {base_spec.get(key)} -> {spec.get(key)}")
    bb, b = base_spec.get("bootstrap") or {}, spec.get("bootstrap") or {}
    if b.get("confidence", 0.95) < bb.get("confidence", 0.95) or b.get("n_resamples", 1000) < bb.get("n_resamples", 1000):
        out.append("RATCHET bootstrap confidence / n_resamples lowered")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        name, metric = s["name"], s["metric"]
        sid = f"slice:{name}/{metric}"
        a = per_item(pr, metric, slice_name=name)
        b = per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict, note = "fail", "slice has no items in PR"
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in s and val < s["min"]:
                verdict = "fail"
            if "max" in s and val > s["max"]:
                verdict = "fail"
            if s.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000),
                                                       rng=bs.get("rng", 2027))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
                else:
                    verdict, note = "fail", "no shared items with baseline"
        if verdict != "ok" and s.get("blocking"):
            blocked.append(sid)
        label = "ok" if verdict == "ok" else ("BLOCK" if s.get("blocking") else "warn")
        th = {k: s[k] for k in ("min", "max", "no_significant_drop") if k in s}
        lines.append(f"  {sid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}"
                     f"  ({len(a)} item)")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
