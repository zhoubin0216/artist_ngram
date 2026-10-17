#!/usr/bin/env python3
"""Stage 5 read-only synthesis of frozen A3, B and C outputs.

Creates publication figures + a checked figure-data CSV in a NEW output directory.
No model fitting, API access, or changes to earlier experiments.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BINS = [-30, -20, -10, 0, 10, 20, 30, 40]

# Shared bright palette for all Section 5 figures. Each source keeps its own
# original measurement unit; color is presentation only, never a data change.
CLUSTER_COLORS = {0: '#3B82F6', 1: '#F59E0B'}       # bright blue / amber
NGRAM_COLOR = '#3B82F6'                            # blue
OPENALEX_COLOR = '#10B981'                         # emerald green
GETTY_SALE_COLOR = '#F43F5E'                       # rose
GETTY_CATALOG_COLOR = '#8B5CF6'                    # violet
BOTH_ANY_COLOR = '#A78BFA'                         # light violet
BOTH_SIGNAL_COLOR = '#FBBF24'                      # yellow/amber
GRID_COLOR = '#CBD5E1'
DEATH_LINE_COLOR = '#64748B'


def apply_bright_style():
    """Clear, restrained white-background styling with consistent bright colors."""
    plt.rcParams.update({
        'font.size': 9,
        'axes.spines.top': False,
        'axes.spines.right': False,
        'axes.facecolor': 'white',
        'figure.facecolor': 'white',
        'axes.labelcolor': '#334155',
        'text.color': '#1E293B',
        'xtick.color': '#475569',
        'ytick.color': '#475569',
        'savefig.facecolor': 'white',
    })
DEFAULT_CASES = ['Q5589', 'Q156272']  # Henri Matisse, André Derain
REQUIRED = {
    'a3_assign': ['artist_id', 'death_cluster_id'],
    'a3_features': ['artist_id', 'canonical_name', 'death_year', 'identity_warning'],
    'b_summary': ['artist_id', 'canonical_name', 'death_year', 'death_cluster_id', 'identity_warning', 'openalex_signal_sufficient'],
    'b_decades': ['artist_id', 'death_cluster_id', 'relative_bin_start', 'works_count', 'denominator_works', 'years_observed', 'works_per_10000'],
    'c_coverage': ['artist_id', 'canonical_name', 'death_year', 'cluster_id', 'accepted_mapping', 'identity_warning', 'getty_window_signal_sufficient', 'window_dated_events'],
    'c_decades': ['artist_id', 'cluster_id', 'accepted_mapping', 'relative_bin_start', 'confirmed_sale_count', 'catalog_entry_count', 'total_record_count', 'record_state'],
}


def read_table(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f'File not found: {path}')
    df = pd.read_csv(path, encoding='utf-8-sig', dtype={'artist_id': 'string'})
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f'{path}: missing fields {sorted(missing)}')
    return df


def require_unique(df: pd.DataFrame, keys: list[str], label: str) -> None:
    if df[keys].isna().any().any() or df.duplicated(keys).any():
        raise ValueError(f'{label}: null or duplicated keys {keys}')


def to_bool(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ('true', '1', 'yes'):
        return True
    if s in ('false', '0', 'no'):
        return False
    raise ValueError(f'Expected explicit boolean, got {value!r}')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fd:
        for chunk in iter(lambda: fd.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def new_run_dir(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    base = 'run_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    for suffix in range(100):
        d = out / (base if suffix == 0 else f'{base}_{suffix}')
        try:
            d.mkdir(exist_ok=False)
            (d / 'figures').mkdir()
            return d
        except FileExistsError:
            pass
    raise FileExistsError('Cannot allocate fresh output folder')


def check_bins(df: pd.DataFrame, id_: str, name: str):
    rows = df.loc[df['artist_id'] == id_].copy()
    if len(rows) != 8 or sorted(rows['relative_bin_start'].astype(int).tolist()) != BINS:
        raise ValueError(f'{name}: expected precisely 8 unique -30..+40 decade rows')
    return rows.sort_values('relative_bin_start')


def load_and_validate(a3: Path, b: Path, c: Path):
    paths = {
        'a3_assign': a3 / 'death_aligned_assignments.csv',
        'a3_features': a3 / 'death_aligned_features.csv',
        'a3_summary': a3 / 'comparison_summary.json',
        'b_summary': b / 'cross_source_artist_summary.csv',
        'b_decades': b / 'openalex_relative_decades.csv',
        'b_report': b / 'comparison_summary.json',
        'c_coverage': c / 'getty_artist_coverage.csv',
        'c_decades': c / 'getty_relative_decades.csv',
        'c_report': c / 'comparison_summary.json',
    }
    for p in paths.values():
        if not p.is_file():
            raise FileNotFoundError(p)
    tables = {k: read_table(paths[k], v) for k, v in REQUIRED.items()}
    for key in ('a3_assign', 'a3_features', 'b_summary', 'c_coverage'):
        require_unique(tables[key], ['artist_id'], key)
    for key in ('b_decades', 'c_decades'):
        require_unique(tables[key], ['artist_id', 'relative_bin_start'], key)
    ids = [set(tables[key].artist_id) for key in ('a3_assign', 'a3_features', 'b_summary', 'c_coverage')]
    if not all(s == ids[0] for s in ids):
        raise ValueError('A3 / B / C artist QID sets differ; choose matching frozen runs')
    a3_report = json.loads(paths['a3_summary'].read_text(encoding='utf-8'))
    b_report = json.loads(paths['b_report'].read_text(encoding='utf-8'))
    c_report = json.loads(paths['c_report'].read_text(encoding='utf-8'))
    n = len(ids[0]); assert n > 0
    if not (a3_report['selection']['eligible_for_both_models'] == b_report['artist_count'] == c_report['a3_artists'] == n):
        raise ValueError('A3/B/C summary artist counts do not agree with tables')
    counts = {str(int(k)): int(v) for k, v in tables['a3_assign'].death_cluster_id.value_counts().items()}
    expected = {str(k): int(v) for k,v in a3_report['death_aligned']['cluster_sizes'].items()}
    if counts != expected:
        raise ValueError(f'A3 cluster sizes disagree: {counts} vs {expected}')
    # Frozen input SHA declarations in B and C are used to validate exact same A3 input.
    h_a3 = sha256(paths['a3_assign'])
    if b_report.get('input_sha256', {}).get('a3_assignments') != h_a3:
        raise ValueError('Stage B was not built with this exact A3 assignments CSV')
    if c_report.get('source_files_sha256', {}).get('a3_assignments') != h_a3:
        raise ValueError('Stage C was not built with this exact A3 assignments CSV')
    return paths, tables, a3_report, b_report, c_report


def build_case_rows(cases, tables):
    ai = tables['a3_assign'].set_index('artist_id')
    af = tables['a3_features'].set_index('artist_id')
    bs = tables['b_summary'].set_index('artist_id')
    cc = tables['c_coverage'].set_index('artist_id')
    if len(set(cases)) != len(cases) or len(cases) != 2:
        raise ValueError('Exactly two DISTINCT case IDs are required')
    recs, meta = [], []
    for id_ in cases:
        if id_ not in ai.index:
            raise ValueError(f'Case {id_} missing from A3/B/C common cohort')
        a, f, b, c = ai.loc[id_], af.loc[id_], bs.loc[id_], cc.loc[id_]
        for obj, field in [(b,'death_cluster_id'), (c,'cluster_id')]:
            if int(obj[field]) != int(a['death_cluster_id']):
                raise ValueError(f'Case {id_} mismatching cluster in source')
        for obj in [b,c]:
            if int(obj['death_year']) != int(f['death_year']):
                raise ValueError(f'Case {id_} inconsistent death year')
        if not (to_bool(b['openalex_signal_sufficient']) and
                to_bool(c['accepted_mapping']) and to_bool(c['getty_window_signal_sufficient'])):
            raise ValueError(f'Case {id_} is not independently signal-eligible in B and C')
        if any(to_bool(x) for x in (f['identity_warning'], b['identity_warning'], c['identity_warning'])):
            raise ValueError(f'Case {id_} has an A2 name-identity warning')
        for obj in [b,c]:
            if obj['canonical_name'] != f['canonical_name']:
                raise ValueError(f'Case {id_} canonical names disagree')
        bro = check_bins(tables['b_decades'],id_,'OpenAlex')
        cro = check_bins(tables['c_decades'],id_,'Getty')
        if int(bro['works_count'].sum()) != int(b['openalex_window_works']):
            raise ValueError(f'OpenAlex decade counts disagree with window total for {id_}')
        if int(cro['total_record_count'].sum()) != int(c['window_dated_events']):
            raise ValueError(f'Getty decade counts disagree with window total for {id_}')
        m = {'artist_id':id_, 'canonical_name':f['canonical_name'], 'death_year':int(f['death_year']),
             'cluster_id':int(a['death_cluster_id']),
             'getty_window_dated_events':int(c['window_dated_events']),
             'openalex_window_works':int(b['openalex_window_works'])}
        meta.append(m)
        for yr in BINS:
            bo = bro.loc[bro['relative_bin_start'].astype(int) == yr].iloc[0]
            co = cro.loc[cro['relative_bin_start'].astype(int) == yr].iloc[0]
            if not to_bool(co['accepted_mapping']) or co['record_state'] != 'accepted_mapping_dated_index':
                raise ValueError(f'Getty missing/non-accepted values for {id_} bin {yr}')
            if int(bo['years_observed']) != 10 or int(bo['denominator_works']) <= 0:
                raise ValueError(f'OpenAlex denominator/year coverage invalid for {id_} bin {yr}')
            nr = float(b[f'ngram_rate_rel_{yr}'])
            ar = float(b[f'openalex_rate_rel_{yr}'])
            fr = float(f[f'rate_rel_{yr}'])
            if not math.isclose(nr, fr, abs_tol=1e-8, rel_tol=1e-9):
                raise ValueError(f'Ngram A3/B derived decadal values disagree for {id_} bin {yr}')
            if not math.isclose(ar, float(bo['works_per_10000']), abs_tol=1e-8, rel_tol=1e-9):
                raise ValueError(f'OpenAlex B sources disagree for {id_} bin {yr}')
            sv,cv,tv = [int(co[x]) for x in ['confirmed_sale_count','catalog_entry_count','total_record_count']]
            if sv+cv != tv:
                raise ValueError(f'Getty sale/catalog counts disagree for {id_} bin {yr}')
            recs.append({**m, 'relative_bin_start':yr, 'relative_bin_end':yr+9,
                         'ngram_match_per_billion':nr, 'openalex_works_per_10000':ar,
                         'openalex_works_count':int(bo['works_count']),
                         'openalex_denominator_works':int(bo['denominator_works']),
                         'getty_confirmed_title_transfers':sv,'getty_catalog_entries':cv,
                         'getty_dated_records':tv})
    if int(meta[0]['death_year']) != int(meta[1]['death_year']):
        raise ValueError('Selected examples must share a death year for matched-era demonstration')
    return pd.DataFrame(recs), meta


def a3_centers_chart(tables: dict, path: Path) -> None:
    """Rebuild *display-only* A3 centers from the frozen feature vectors + labels.

    A3 used log1p and per-artist z-standardization (population std, ddof=0).
    A K-Means center is the arithmetic mean of assigned feature vectors. Thus
    no model is retrained here, and the original cluster assignments are frozen.
    """
    apply_bright_style()
    rates = tables['a3_features'][['artist_id'] + [f'rate_rel_{b}' for b in BINS]].copy()
    joined = tables['a3_assign'][['artist_id','death_cluster_id']].merge(
        rates, on='artist_id', how='inner', validate='one_to_one')
    if len(joined) != len(tables['a3_assign']) or len(joined) != len(rates):
        raise ValueError('A3 feature / assignment artist sets do not match')
    raw = joined[[f'rate_rel_{b}' for b in BINS]].apply(pd.to_numeric, errors='raise').to_numpy(dtype=float)
    if not np.isfinite(raw).all() or (raw < 0).any():
        raise ValueError('A3 features contain negative or non-finite rates')
    transformed = np.log1p(raw)
    sd = transformed.std(axis=1, keepdims=True)
    if (sd < 1e-12).any():
        raise ValueError('A3 has a constant shape; cannot recover original z-scores')
    z = (transformed - transformed.mean(axis=1, keepdims=True)) / sd
    cluster_ids = pd.to_numeric(joined['death_cluster_id'], errors='raise').astype(int).to_numpy()
    unique = sorted(set(cluster_ids))
    if unique != [0, 1]:
        raise ValueError(f'Expected frozen A3 K=2 groups 0,1; got {unique}')

    x = np.array(BINS, dtype=float) + 4.5
    fig, ax = plt.subplots(figsize=(9.4, 4.8), constrained_layout=True)
    names = {0: 'Cluster 0: posthumous growth',
             1: 'Cluster 1: pre-death peak'}
    styles = {0: ('-', 'o'), 1: ('--', 's')}
    for k in unique:
        subset = z[cluster_ids == k]
        linestyle, marker = styles[k]
        ax.plot(x, subset.mean(axis=0), linestyle=linestyle, marker=marker,
                color=CLUSTER_COLORS[k], linewidth=2.6, markersize=6,
                label=f'{names[k]} (n={len(subset)})')
    ax.axvline(0, linestyle=':', linewidth=1.4, color=DEATH_LINE_COLOR,
               label='Year of death')
    ax.axhline(0, linewidth=.8, color=GRID_COLOR)
    ax.set_xlim(-31, 50)
    ax.set_xticks([-30, -20, -10, 0, 10, 20, 30, 40])
    ax.grid(axis='y', color=GRID_COLOR, alpha=.52, linewidth=.7)
    ax.set(xlabel='Years relative to death (10-year bin midpoint)',
           ylabel='Within-artist z-score of log1p(Ngram rate)',
           title='Ngram visibility patterns aligned by year of death')
    ax.legend(loc='best', frameon=False, fontsize=8.5)
    fig.savefig(path, dpi=210, bbox_inches='tight')
    plt.close(fig)


def case_chart(case_df: pd.DataFrame, meta, path: Path) -> None:
    apply_bright_style()
    fig, axs = plt.subplots(2, 3, figsize=(12.6, 6.5), sharex=True, constrained_layout=True)
    for i, m in enumerate(meta):
        rows = case_df[case_df.artist_id == m['artist_id']].sort_values('relative_bin_start')
        x = rows.relative_bin_start.to_numpy(dtype=int) + 4.5  # bin midpoint
        ax = axs[i, 0]
        ax.plot(x, rows.ngram_match_per_billion, '-o',
                color=NGRAM_COLOR, lw=2.2, ms=5)
        ax.set_ylabel(f"{m['canonical_name']}\n(Cluster {m['cluster_id']})\n\nNgram / billion")
        ax = axs[i, 1]
        ax.plot(x, rows.openalex_works_per_10000, '-s',
                color=OPENALEX_COLOR, lw=2.2, ms=5)
        ax.set_ylabel('OpenAlex works / 10k')
        ax = axs[i, 2]
        w = 3.5
        ax.bar(x - w / 2, rows.getty_confirmed_title_transfers,
               width=w, color=GETTY_SALE_COLOR, alpha=.93,
               label='Confirmed title transfer')
        ax.bar(x + w / 2, rows.getty_catalog_entries,
               width=w, color=GETTY_CATALOG_COLOR, alpha=.93,
               label='Catalog entry')
        ax.set_ylabel('Getty dated records / 10y')
        for j in range(3):
            a = axs[i, j]
            a.axvline(0, ls=':', lw=1.2, color=DEATH_LINE_COLOR)
            a.set_xlim(-31, 50)
            a.set_xticks([-30, -20, -10, 0, 10, 20, 30, 40])
            a.grid(axis='y', color=GRID_COLOR, alpha=.48, linewidth=.7)
            a.set_axisbelow(True)
    for j, title in enumerate(['Google Books Ngram', 'OpenAlex (art-related works)',
                               'Getty Provenance (indexed events)']):
        axs[0, j].set_title(title, fontweight='bold')
        axs[1, j].set_xlabel('Years relative to death (0 = death)')
    axs[0, 2].legend(loc='upper right', fontsize=7, frameon=False)
    fig.suptitle('Artist visibility across three sources: Matisse and Derain',
                 fontsize=12, weight='bold')
    fig.savefig(path, dpi=210, bbox_inches='tight')
    plt.close(fig)


def coverage_chart(b_report: dict, c_report: dict, path: Path) -> None:
    apply_bright_style()
    nb = int(b_report['artist_count'])
    na = int(b_report['openalex_artist_with_any_works'])
    oa = int(b_report['openalex_artist_with_signal'])
    ng = int(c_report['a3_with_dated_event_in_window'])
    gs = int(c_report['a3_with_getty_trend_signal'])
    both = int(c_report['three_source_any_overlap'])
    both_s = int(c_report['three_source_both_signal_sufficient'])
    # Separate source-specific coverage bars are NOT a nested sequence.
    items = [
        ('OpenAlex: any matched work', na, OPENALEX_COLOR),
        ('Getty: dated event', ng, GETTY_SALE_COLOR),
        ('Both: any record', both, BOTH_ANY_COLOR),
        ('Both: trend eligible', both_s, BOTH_SIGNAL_COLOR),
    ]
    fig, ax = plt.subplots(figsize=(9.7, 4.4))
    fig.subplots_adjust(left=.38, right=.92, top=.83, bottom=.26)
    labels = [x[0] for x in items][::-1]
    values = [x[1] for x in items][::-1]
    colors = [x[2] for x in items][::-1]
    ax.barh(labels, values, color=colors, height=.65)
    for i, val in enumerate(values):
        ax.text(val + 4, i, f'{val}  ({val/nb*100:.1f}%)', va='center', fontsize=9)
    ax.set_xlim(0, max(values)*1.36)
    ax.set_xlabel(f'Artists in Ngram A3 cohort (n={nb})')
    ax.set_title('Cross-source coverage and joint availability', pad=10, weight='bold')
    ax.grid(axis='x', color=GRID_COLOR, alpha=.4)
    ax.set_axisbelow(True)
    fig.text(.04, .08,
             f'OpenAlex trend-eligible: {oa} artists; Getty trend-eligible: {gs} artists.\n'
             'OpenAlex and Getty single-source bars are not nested; "Both" is their intersection.',
             fontsize=8, va='center')
    fig.savefig(path, dpi=210, bbox_inches='tight')
    plt.close(fig)


def run(args):
    a3,b,c=map(Path,[args.a3_run,args.stage_b_run,args.stage_c_run])
    paths,tables,a3rep,brep,crep=load_and_validate(a3,b,c)
    case_ids=[x.strip() for x in args.cases.split(',') if x.strip()]
    case_df,meta=build_case_rows(case_ids,tables)
    output=new_run_dir(Path(args.output_root))
    case_df.to_csv(output/'case_study_data.csv',index=False,encoding='utf-8-sig')
    case_chart(case_df,meta,output/'figures/three_source_case_studies.png')
    coverage_chart(brep,crep,output/'figures/three_source_coverage.png')
    a3_centers_chart(tables, output/'figures/a3_death_aligned_centers.png')
    all_inputs={str(k):{'path':str(v.resolve()),'sha256':sha256(v)} for k,v in paths.items()}
    report={'title':'Stage 5 final synthesis (A3+B+C, bright figures; no retraining)',
            'created_at_utc':datetime.now(timezone.utc).isoformat(),
            'a3_n':int(a3rep['selection']['eligible_for_both_models']),
            'a3_clusters':a3rep['death_aligned']['cluster_sizes'],
            'a3_k':int(a3rep['death_aligned']['k']),
            'a3_silhouette':float(a3rep['death_aligned']['silhouette']),
            'a3_initialization_ARI':float(a3rep['death_aligned']['initialization_ARI']),
            'a3_warning_count':int(a3rep['selection']['identity_warnings_in_eligible']),
            'a3_ARI_calendar_vs_death':float(a3rep['comparison']['ARI_calendar_vs_death']),
            'openalex_any':int(brep['openalex_artist_with_any_works']),
            'openalex_signal':int(brep['openalex_artist_with_signal']),
            'getty_accepted_mapping':int(crep['a3_accepted_getty_mappings']),
            'getty_dated_window':int(crep['a3_with_dated_event_in_window']),
            'getty_signal':int(crep['a3_with_getty_trend_signal']),
            'both_any':int(crep['three_source_any_overlap']),
            'both_signal':int(crep['three_source_both_signal_sufficient']),
            'getty_full_event_rows':int(crep['getty_full_dataset_event_rows']),
            'getty_full_undated_event_rows':int(crep['getty_full_dataset_events_without_year']),
            'cases':meta,'inputs':all_inputs,
            'warnings':['Cross-source matches/coverage do not establish causal influence.',
                        'Getty counts are NOT normalized against a global denominator.',
                        'Ngram, OpenAlex and Getty units are separate: plots use different y axes.',
                        'Only accepted Getty mappings; undated events are not assigned to bins.',
                        'Cases chosen for matching death year and source coverage, not population representativeness.']}
    (output/'final_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    readme=(f"# Final Section 5 evidence export\n\nCreated: {report['created_at_utc']}\n\n"
            "Files:\n- `case_study_data.csv`: exact plotted values, rates and numerators for two cases.\n"
            "- `figures/three_source_case_studies.png`: two persons × three sources; independent vertical scales; dotted death line.\n"
            "- `figures/three_source_coverage.png`: OpenAlex / Getty sample coverage in the A3 cohort.\n"
            "- `figures/a3_death_aligned_centers.png`: presentation-only redraw from frozen A3 feature vectors and assignments; no retraining.\n"
            "- `final_summary.json`: source counts and SHA-256 provenance.\n\n"
            "Do not add different-source counts; undated Getty events cannot be placed on a timeline.\n")
    (output/'README_RESULTS.md').write_text(readme,encoding='utf-8')
    print(json.dumps({'status':'ok','output':str(output),'cases':meta,'A3_n':report['a3_n'],
                      'both_signal':report['both_signal']},indent=2,ensure_ascii=False))
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a3-run',required=True)
    parser.add_argument('--stage-b-run',required=True)
    parser.add_argument('--stage-c-run',required=True)
    parser.add_argument('--cases',default=','.join(DEFAULT_CASES),help='two QIDs with same death year')
    parser.add_argument('--output-root',default='results/ml/section5_final')
    args=parser.parse_args()
    run(args)

if __name__=='__main__':
    main()
