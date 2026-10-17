"""Stage C: descriptive Getty validation of FIXED Ngram A3 life-cycle clusters.

Run at artist_ngram repository root. Does NOT retrain and does NOT mutate inputs.
Data are a selected provenance index, NOT a complete census of transactions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

START, END, WIDTH = -30, 49, 10
BINS = list(range(START, END + 1, WIDTH))
EXPECTED_EVENT_TYPES = {'confirmed_sale', 'catalog_entry'}


def load_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f'Missing input: {path}')
    return pd.read_csv(path, encoding='utf-8-sig', dtype={'artist_id': 'string'}, low_memory=False)


def required(frame: pd.DataFrame, names: list[str], path: str) -> None:
    absent = set(names) - set(frame.columns)
    if absent:
        raise ValueError(f'{path} missing columns: {sorted(absent)}')


def bool_series(series: pd.Series, name: str) -> pd.Series:
    normalized = series.astype('string').str.strip().str.lower()
    values = {'true': True, 'false': False, '1': True, '0': False}
    if normalized.isna().any() or not normalized.isin(values).all():
        bad = normalized[~normalized.isin(values)].drop_duplicates().tolist()
        raise ValueError(f'Invalid boolean values in {name}: {bad[:8]}')
    return normalized.map(values).astype(bool)


def integer_series(series: pd.Series, name: str, *, nonnegative: bool = False,
                   allow_missing: bool = False) -> pd.Series:
    raw = series.astype('string').str.strip()
    raw = raw.replace('', pd.NA)
    parsed = pd.to_numeric(raw, errors='coerce')
    if (not allow_missing and parsed.isna().any()) or ((raw.notna()) & parsed.isna()).any():
        raise ValueError(f'Non-numeric or missing values in {name}')
    if (parsed.dropna() != np.floor(parsed.dropna())).any():
        raise ValueError(f'Non-integer values in {name}')
    if nonnegative and (parsed.dropna() < 0).any():
        raise ValueError(f'Negative counts in {name}')
    return parsed.astype('Int64')


def check_unique(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    if frame[columns].isna().any().any() or frame.duplicated(columns).any():
        raise ValueError(f'Missing or duplicate keys in {name}: {columns}')


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def interval_counts(group: pd.DataFrame, flag: str, denom_col: str = 'accepted_mapping') -> dict:
    denom = int(group[denom_col].sum()) if denom_col else len(group)
    numer = int(group[flag].sum())
    return {'count': numer, 'denominator': denom, 'pct': round(numer / denom * 100, 2) if denom else None}


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if not n:
        return None, None
    p = k / n
    den = 1 + z*z/n
    center = (p + z*z/(2*n)) / den
    rad = z/den*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return center-rad, center+rad


def make_output_directory(base: Path) -> Path:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    target = base / f'run_{stamp}'
    target.mkdir(parents=True, exist_ok=False)
    (target / 'figures').mkdir()
    return target


def verify_event_annual_consistency(events: pd.DataFrame, annual: pd.DataFrame) -> None:
    # Getty's own pipeline defines annual totals as grouped dated event records.
    dated = events.dropna(subset=['event_year']).copy()
    dated['confirmed_sale_count'] = (dated.event_type == 'confirmed_sale').astype(int)
    dated['catalog_entry_count'] = (dated.event_type == 'catalog_entry').astype(int)
    dated['total_record_count'] = 1
    columns = ['confirmed_sale_count', 'catalog_entry_count', 'total_record_count']
    reagg = dated.groupby(['artist_id', 'event_year'], as_index=False)[columns].sum()
    joined = reagg.merge(annual, on=['artist_id', 'event_year'], how='outer', suffixes=('_detail', '_year'), indicator=True)
    bad_key = joined['_merge'] != 'both'
    bad_count = pd.Series(False, index=joined.index)
    for col in columns:
        bad_count |= joined[f'{col}_detail'] != joined[f'{col}_year']
    if bad_key.any() or bad_count.fillna(True).any():
        example = joined.loc[bad_key | bad_count.fillna(True)].head(3).to_dict('records')
        raise ValueError('Getty annual counts disagree with dated event detail. Examples: ' + str(example))


def plot_bar_coverage(coverage: pd.DataFrame, path: Path) -> None:
    x = np.arange(len(coverage))
    categories = ['mapping_accepted_pct', 'window_any_pct', 'sales_any_pct', 'catalog_any_pct']
    names = ['Accepted Getty mapping', 'Dated event in window', 'Confirmed sale in window', 'Catalog entry in window']
    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.18
    for i, (key, label) in enumerate(zip(categories, names)):
        ax.bar(x + (i-1.5)*width, coverage[key], width, label=label)
    ax.set_xticks(x, [f'Cluster {v}' for v in coverage.cluster_id])
    ax.set_ylim(0, 100)
    ax.set_ylabel('% of all Ngram A3 artists in that cluster')
    ax.set_title('Getty coverage: denominator is all A3 artists')
    ax.legend(fontsize=8)
    ax.grid(axis='y', alpha=.18)
    fig.tight_layout(); fig.savefig(path, dpi=180); plt.close(fig)


def plot_relative_prevalence(profiles: pd.DataFrame, path: Path) -> None:
    fig, axs = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for ax, typ, title in zip(axs, ['sale', 'catalog'], ['Confirmed title-transfer record', 'Catalog/source entry']):
        for cluster, subset in profiles.groupby('cluster_id', sort=True):
            ax.plot(subset.relative_bin_start, subset[f'{typ}_prevalence_mapped_pct'], marker='o', label=f'Cluster {cluster}')
        ax.axvline(0, color='gray', linestyle=':', lw=1, label='Death year' if typ == 'sale' else None)
        ax.set_ylabel('% of accepted mapped artists')
        ax.set_title(title)
        ax.grid(alpha=.2); ax.legend()
    axs[-1].set_xticks(BINS, [f'{x:+d}' for x in BINS]); axs[-1].set_xlabel('Years relative to death (start of 10-year bin)')
    fig.suptitle('Dated Getty event prevalence (not transaction probability)', fontsize=12)
    fig.tight_layout(); fig.savefig(path, dpi=180); plt.close(fig)


def plot_counts(profiles: pd.DataFrame, path: Path) -> None:
    fig, axs = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for ax, kind, title in zip(axs, ['sale', 'catalog'], ['Confirmed title-transfer records', 'Catalog/source records']):
        for cluster, subset in profiles.groupby('cluster_id', sort=True):
            ax.plot(subset.relative_bin_start, subset[f'{kind}_mean_mapped'], marker='o', label=f'Cluster {cluster}')
        ax.axvline(0, color='gray', linestyle=':', lw=1)
        ax.set_ylabel('Mean dated records per accepted mapped artist')
        ax.set_title(title + ' (descriptive, outlier-sensitive)')
        ax.grid(alpha=.2); ax.legend()
    axs[-1].set_xticks(BINS, [f'{x:+d}' for x in BINS]); axs[-1].set_xlabel('Years relative to death')
    fig.tight_layout(); fig.savefig(path, dpi=180); plt.close(fig)


def plot_overlap(overlap: pd.DataFrame, path: Path) -> None:
    cols = ['openalex_any_pct', 'getty_window_any_pct', 'both_any_pct', 'three_source_trend_pct']
    names = ['OpenAlex any', 'Getty dated any', 'Both any', 'Both trend-qualified']
    x = np.arange(len(overlap)); width=.18
    fig, ax = plt.subplots(figsize=(10, 5))
    for i,(col, name) in enumerate(zip(cols,names)):
        ax.bar(x + (i-1.5)*width, overlap[col], width, label=name)
    ax.set_xticks(x,[f'Cluster {int(c)}' for c in overlap.cluster_id]);ax.set_ylim(0,100)
    ax.set_ylabel('% of all Ngram A3 artists in cluster')
    ax.set_title('Three-source observation overlap, not joint impact score')
    ax.grid(axis='y',alpha=.18);ax.legend(fontsize=8)
    fig.tight_layout();fig.savefig(path,dpi=180);plt.close(fig)


def run_analysis(*, a3_dir: Path, getty_dir: Path, output_root: Path,
                 stage_b: Path | None = None, min_window_records: int = 3,
                 min_nonzero_bins: int = 2) -> tuple[Path, dict]:
    if min_window_records < 1 or min_nonzero_bins < 1 or min_nonzero_bins > len(BINS):
        raise ValueError('Invalid signal thresholds')
    assignment_path = a3_dir / 'death_aligned_assignments.csv'
    feature_path = a3_dir / 'death_aligned_features.csv'
    mapping_path = getty_dir / 'getty_artist_mapping.csv'
    annual_path = getty_dir / 'getty_artist_year.csv'
    events_path = getty_dir / 'getty_events.csv'
    a = load_csv(assignment_path)
    f = load_csv(feature_path)
    m = load_csv(mapping_path)
    y = load_csv(annual_path)
    e = load_csv(events_path)
    required(a, ['artist_id', 'death_cluster_id'], 'A3 assignments')
    required(f, ['artist_id', 'canonical_name', 'birth_year', 'death_year', 'identity_warning'], 'A3 features')
    required(m, ['artist_id', 'getty_person_uri', 'manual_review', 'match_confidence'], 'Getty mapping')
    required(y, ['artist_id', 'event_year', 'confirmed_sale_count', 'catalog_entry_count', 'total_record_count'], 'Getty annual')
    required(e, ['artist_id', 'event_year', 'event_type'], 'Getty event detail')
    for name, df, keys in [('A3 assignments',a,['artist_id']),('A3 features',f,['artist_id']),('Getty mapping',m,['artist_id']),('Getty annual',y,['artist_id','event_year'])]:
        check_unique(df, keys, name)
    if set(a.artist_id) != set(f.artist_id):
        raise ValueError('A3 assignment and feature artist sets differ')
    a['cluster_id'] = integer_series(a['death_cluster_id'], 'A3 cluster').astype(int)
    f['death_year'] = integer_series(f['death_year'], 'A3 death year')
    f['birth_year'] = integer_series(f['birth_year'], 'A3 birth year', allow_missing=True)
    f['identity_warning'] = bool_series(f['identity_warning'], 'A3 identity warning')
    x = a[['artist_id','cluster_id']].merge(f[['artist_id','canonical_name','birth_year','death_year','identity_warning']], on='artist_id',validate='one_to_one')
    if x.death_year.isna().any() or not ((x.death_year-30>=1800)&(x.death_year+49<=2019)).all():
        raise ValueError('A3 sample does not have fully observable -30:+49 window')
    m['manual_review'] = bool_series(m['manual_review'], 'Getty manual_review')
    m['match_confidence'] = pd.to_numeric(m['match_confidence'], errors='coerce')
    uri = m.getty_person_uri.fillna('').astype(str).str.strip()
    if ((uri != '') & (m.manual_review | (m.match_confidence < .80))).any():
        raise ValueError('Getty mapping contains accepted URI with review/confidence inconsistency')
    m['accepted_mapping'] = (uri != '') & ~m.manual_review & (m.match_confidence >= .8)
    x = x.merge(m[['artist_id','accepted_mapping','manual_review','match_confidence']],on='artist_id',how='left',validate='one_to_one',indicator=True)
    if (x['_merge']!='both').any():
        raise ValueError('Getty mapping does not include all 575 A3 artists')
    x=x.drop(columns='_merge'); x['accepted_mapping']=x.accepted_mapping.astype(bool)
    y['event_year']=integer_series(y.event_year,'Getty annual event_year')
    for col in ['confirmed_sale_count','catalog_entry_count','total_record_count']:
        y[col]=integer_series(y[col],col,nonnegative=True).astype(int)
    if (y.confirmed_sale_count+y.catalog_entry_count!=y.total_record_count).any():
        raise ValueError('Getty annual sale + catalog differs from total')
    e['event_year']=integer_series(e.event_year,'Getty event_year',allow_missing=True)
    if not e.event_type.isin(EXPECTED_EVENT_TYPES).all():
        raise ValueError('Getty event_type includes unknown categories')
    if not set(e.artist_id).issubset(set(m.loc[m.accepted_mapping,'artist_id'])):
        raise ValueError('Getty event detail contains artist without accepted mapping')
    if set(y.artist_id)-set(m.loc[m.accepted_mapping,'artist_id']):
        raise ValueError('Getty annual rows contain artist without accepted mapping')
    verify_event_annual_consistency(e,y)
    observed_all=e.groupby('artist_id').agg(
        total_events=('event_type','size'),
        events_missing_year=('event_year',lambda c:int(c.isna().sum())),
        events_with_year=('event_year',lambda c:int(c.notna().sum()))
    ).reset_index()
    x=x.merge(observed_all,on='artist_id',how='left',validate='one_to_one')
    for name in ['total_events','events_missing_year','events_with_year']:
        x[name]=x[name].fillna(0).astype(int)
    x['dated_fraction_all_events']=np.where(x.total_events>0,x.events_with_year / x.total_events,np.nan)
    # Dated annual records only. Sparse Getty absence means no indexed *dated* event, not zero historical sales.
    joined=y.merge(x[['artist_id','death_year','cluster_id']],on='artist_id',how='inner',validate='many_to_one')
    joined['relative_year']=joined.event_year-joined.death_year
    joined=joined[joined.relative_year.between(START,END)].copy()
    joined['relative_bin_start']=(START+((joined.relative_year-START)//WIDTH)*WIDTH).astype(int)
    sums=joined.groupby(['artist_id','relative_bin_start'],as_index=False)[['confirmed_sale_count','catalog_entry_count','total_record_count']].sum()
    # Keep unmapped cases explicitly missing rather than fabricating zero records.
    full=x[['artist_id','cluster_id','accepted_mapping']].merge(pd.DataFrame({'relative_bin_start':BINS}),how='cross')
    series=full.merge(sums,on=['artist_id','relative_bin_start'],how='left',validate='one_to_one')
    for name in ['confirmed_sale_count','catalog_entry_count','total_record_count']:
        series.loc[series.accepted_mapping & series[name].isna(),name]=0
        series[name]=series[name].astype('Int64')
    series['record_state']=np.where(series.accepted_mapping,'accepted_mapping_dated_index','no_accepted_mapping')
    window_totals=series.loc[series.accepted_mapping].groupby('artist_id').agg(
        window_sales=('confirmed_sale_count','sum'),
        window_catalog=('catalog_entry_count','sum'),
        window_dated_events=('total_record_count','sum'),
        window_nonzero_bins=('total_record_count',lambda t:int((t>0).sum()))
    ).reset_index()
    x=x.merge(window_totals,on='artist_id',how='left',validate='one_to_one')
    for name in ['window_sales','window_catalog','window_dated_events','window_nonzero_bins']:
        x[name]=x[name].fillna(0).astype(int)
    x['has_any_getty_event']=x.total_events>0
    x['has_any_dated_event']=x.events_with_year>0
    x['getty_window_any']=x.window_dated_events>0
    x['getty_window_sale_any']=x.window_sales>0
    x['getty_window_catalog_any']=x.window_catalog>0
    x['getty_window_signal_sufficient']=(x.window_dated_events>=min_window_records)&(x.window_nonzero_bins>=min_nonzero_bins)
    x['getty_status']=np.select(
        [~x.accepted_mapping,x.total_events.eq(0),x.events_with_year.eq(0),~x.getty_window_any],
        ['not_accepted_mapping','accepted_no_event','events_all_undated','dated_events_outside_window'],
        default='dated_events_in_window')
    # Optional Stage B merge. Ngram is covered by construction; third-source overlap uses observed values only.
    overlap=None
    if stage_b is not None:
        b=load_csv(stage_b)
        required(b,['artist_id','death_cluster_id','openalex_any','openalex_signal_sufficient'],'Stage B summary')
        check_unique(b,['artist_id'],'Stage B summary')
        if set(b.artist_id)!=set(x.artist_id):
            raise ValueError('Stage B and A3 artist sets do not match')
        b['death_cluster_id']=integer_series(b.death_cluster_id,'Stage B cluster').astype(int)
        b['openalex_any']=bool_series(b.openalex_any,'Stage B openalex_any')
        b['openalex_signal_sufficient']=bool_series(b.openalex_signal_sufficient,'Stage B openalex_signal_sufficient')
        x=x.merge(b[['artist_id','death_cluster_id','openalex_any','openalex_signal_sufficient']],on='artist_id',how='left',validate='one_to_one')
        if (x.cluster_id!=x.death_cluster_id).any():
            raise ValueError('Stage B cluster disagrees with A3')
        x=x.drop(columns='death_cluster_id')
        x['both_openalex_getty_any']=x.openalex_any & x.getty_window_any
        x['three_source_trend_eligible']=x.openalex_signal_sufficient & x.getty_window_signal_sufficient
        records=[]
        for c,g in x.groupby('cluster_id'):
            n=len(g)
            records.append({'cluster_id':c,'artists':n,
              'openalex_any':int(g.openalex_any.sum()),
              'getty_window_any':int(g.getty_window_any.sum()),
              'both_any':int(g.both_openalex_getty_any.sum()),
              'three_source_trend_eligible':int(g.three_source_trend_eligible.sum()),
              'openalex_any_pct':round(g.openalex_any.mean()*100,2),
              'getty_window_any_pct':round(g.getty_window_any.mean()*100,2),
              'both_any_pct':round(g.both_openalex_getty_any.mean()*100,2),
              'three_source_trend_pct':round(g.three_source_trend_eligible.mean()*100,2)})
        overlap=pd.DataFrame(records)
    # Across all A3 artists, not only Getty mapped ones; avoid denominator changes.
    cover=[]
    for c,g in x.groupby('cluster_id',sort=True):
        row={'cluster_id':int(c),'artists':len(g), 'warning_artists':int(g.identity_warning.sum())}
        for col,prefix in [('accepted_mapping','mapping_accepted'),('getty_window_any','window_any'),
                           ('getty_window_sale_any','sales_any'),('getty_window_catalog_any','catalog_any'),
                           ('getty_window_signal_sufficient','trend_sufficient')]:
            k=int(g[col].sum());lo,hi=wilson_interval(k,len(g))
            row[prefix+'_count']=k; row[prefix+'_pct']=round(100*k/len(g),2)
            row[prefix+'_ci_low_pct']=round(100*lo,2);row[prefix+'_ci_high_pct']=round(100*hi,2)
        matched=g[g.accepted_mapping]
        row['mapped_with_any_dated_window_count']=int(matched.getty_window_any.sum())
        row['mapped_with_any_dated_window_pct']=(round(matched.getty_window_any.mean()*100,2) if len(matched) else None)
        cover.append(row)
    coverage=pd.DataFrame(cover)
    # Group-bin profiles conditional on accepted mapping (not zero-filling unaccepted rows).
    profiles=[]
    for (cluster,b),g in series[series.accepted_mapping].groupby(['cluster_id','relative_bin_start']):
        mapped_n=len(g)
        profiles.append({'cluster_id':int(cluster),'relative_bin_start':int(b),
           'accepted_mapped_artists':mapped_n,
           'sale_prevalence_mapped_pct':float((g.confirmed_sale_count>0).mean()*100),
           'catalog_prevalence_mapped_pct':float((g.catalog_entry_count>0).mean()*100),
           'sale_mean_mapped':float(g.confirmed_sale_count.mean()),
           'catalog_mean_mapped':float(g.catalog_entry_count.mean()),
           'sale_median_mapped':float(g.confirmed_sale_count.median()),
           'catalog_median_mapped':float(g.catalog_entry_count.median()),
           'sale_total':int(g.confirmed_sale_count.sum()),
           'catalog_total':int(g.catalog_entry_count.sum())})
    profiles=pd.DataFrame(profiles)
    # Cohort-aware descriptive table; avoid implying any causal association.
    x['death_cohort_start']=(x.death_year.astype(int)//25)*25
    cohorts=[]
    for (cohort,c),g in x.groupby(['death_cohort_start','cluster_id']):
        cohorts.append({'death_cohort_start':int(cohort),'cluster_id':int(c), 'artists':len(g),
                        'accepted_mapping':int(g.accepted_mapping.sum()),'dated_window_any':int(g.getty_window_any.sum()),
                        'mapping_pct':round(g.accepted_mapping.mean()*100,2),
                        'dated_window_any_pct':round(g.getty_window_any.mean()*100,2)})
    cohorts=pd.DataFrame(cohorts)
    safe=x[~x.identity_warning].copy()
    sensitivity=[]
    for c,g in safe.groupby('cluster_id'):
        sensitivity.append({'cluster_id':int(c),'unflagged_artists':len(g),
           'mapping_accepted':int(g.accepted_mapping.sum()),'dated_window_any':int(g.getty_window_any.sum()),
           'mapping_pct':round(g.accepted_mapping.mean()*100,2),
           'dated_window_any_pct':round(g.getty_window_any.mean()*100,2)})
    sensitivity=pd.DataFrame(sensitivity)
    # Coverage-first *case candidates*, not centroid-defined representatives.
    cases=x[x.accepted_mapping & x.getty_window_any & ~x.identity_warning].sort_values(
        ['cluster_id','window_dated_events','artist_id'],ascending=[True,False,True]).groupby('cluster_id').head(10)
    out=make_output_directory(output_root)
    x.sort_values('artist_id').to_csv(out/'getty_artist_coverage.csv',index=False,encoding='utf-8-sig')
    series.sort_values(['artist_id','relative_bin_start']).to_csv(out/'getty_relative_decades.csv',index=False,encoding='utf-8-sig')
    coverage.to_csv(out/'coverage_by_ngram_cluster.csv',index=False,encoding='utf-8-sig')
    profiles.to_csv(out/'getty_group_relative_profiles.csv',index=False,encoding='utf-8-sig')
    cohorts.to_csv(out/'death_cohort_getty_coverage.csv',index=False,encoding='utf-8-sig')
    sensitivity.to_csv(out/'coverage_without_name_warnings.csv',index=False,encoding='utf-8-sig')
    cases.to_csv(out/'getty_case_candidates.csv',index=False,encoding='utf-8-sig')
    if overlap is not None:
        overlap.to_csv(out/'three_source_overlap.csv',index=False,encoding='utf-8-sig')
    plot_bar_coverage(coverage,out/'figures/getty_coverage_by_cluster.png')
    plot_relative_prevalence(profiles,out/'figures/getty_event_prevalence_relative.png')
    plot_counts(profiles,out/'figures/getty_event_counts_relative.png')
    if overlap is not None:plot_overlap(overlap,out/'figures/three_source_coverage.png')
    source_paths={'a3_assignments':assignment_path,'a3_features':feature_path,'getty_mapping':mapping_path,
                  'getty_annual':annual_path,'getty_events':events_path}
    if stage_b is not None:source_paths['stage_b']=stage_b
    summary={'stage':'C: Getty full-run descriptive validation against fixed Ngram A3 death-aligned clusters',
             'not_retraining':True,'relative_window_inclusive':[START,END],'relative_bins':BINS,
             'source_files_sha256':{k:file_hash(p) for k,p in source_paths.items()},
             'a3_artists':len(x),'clusters':{str(k):int(v) for k,v in x.cluster_id.value_counts().sort_index().items()},
             'a3_accepted_getty_mappings':int(x.accepted_mapping.sum()),
             'a3_any_getty_events':int(x.has_any_getty_event.sum()),
             'a3_any_dated_getty_event_any_year':int(x.has_any_dated_event.sum()),
             'a3_with_dated_event_in_window':int(x.getty_window_any.sum()),
             'a3_with_sale_in_window':int(x.getty_window_sale_any.sum()),
             'a3_with_catalog_in_window':int(x.getty_window_catalog_any.sum()),
             'a3_with_getty_trend_signal':int(x.getty_window_signal_sufficient.sum()),
             'a3_window_confirmed_sale_records':int(x.window_sales.sum()),
             'a3_window_catalog_records':int(x.window_catalog.sum()),
             'a3_total_event_rows_with_no_year':int(x.events_missing_year.sum()),
             'a3_total_event_rows_with_year':int(x.events_with_year.sum()),
             'getty_full_dataset_event_rows':len(e),
             'getty_full_dataset_events_without_year':int(e.event_year.isna().sum()),
             'getty_full_dataset_events_with_year':int(e.event_year.notna().sum()),
             'getty_full_dataset_dated_event_count_matches_yearly_csv':True,
             'signal_rule':{'min_dated_window_records':min_window_records,'min_nonzero_bins':min_nonzero_bins},
             'three_source_overlap_present':overlap is not None,
             'three_source_any_overlap':int(x.both_openalex_getty_any.sum()) if overlap is not None else None,
             'three_source_both_signal_sufficient':int(x.three_source_trend_eligible.sum()) if overlap is not None else None,
             'cautions':['Getty mappings not automatically accepted are MISSING, never interpreted as zero transactions.',
                        'No dated Getty record means no indexed dated event; NOT zero historical transactions.',
                        'Undated event records excluded from all calendar/death-aligned bins.',
                        'Getty confirmed title-transfer records and catalog/source entries are counted separately.',
                        'Dated Getty coverage and indexing vary historically and by artist; no causal interpretation.',
                        'Getty counts are not normalized against a complete annual global-market denominator.',
                        'A3 sample is selected by Ngram coverage and signal; conclusions are conditional on selection.',
                        'OpenAlex and Getty missingness/coverage cannot be treated as the same construct.']}
    (out/'comparison_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    def fmt(row, key):return f"{int(row[key+'_count'])}/{int(row['artists'])} ({row[key+'_pct']:.1f}%)"
    lines=['# Stage C — Getty validation of fixed Ngram A3 clusters','',
           '## Scope and denominator','',
           f'- A3 artists: **{len(x)}**; cluster sizes: '+', '.join(f'{k}: {v}' for k,v in summary['clusters'].items())+'.',
           f'- Accepted Getty mappings in A3: **{summary["a3_accepted_getty_mappings"]}**.',
           f'- Artists with dated Getty events inside death -30:+49: **{summary["a3_with_dated_event_in_window"]}**.',
           f'- Getty full dataset events without usable year: **{summary["getty_full_dataset_events_without_year"]}/{len(e)}**.',
           '', '## Coverage by cluster (denominator: ALL A3 artists)','',
           '| Ngram cluster | Getty matched | Dated event in window | Confirmed sale | Catalog entry |','|---|---:|---:|---:|---:|']
    for _,g in coverage.iterrows():
        lines.append(f"| {int(g.cluster_id)} | {fmt(g,'mapping_accepted')} | {fmt(g,'window_any')} | {fmt(g,'sales_any')} | {fmt(g,'catalog_any')} |")
    if overlap is not None:
        lines +=['','## Three-source observation overlap','',
                 f'- Both OpenAlex and Getty have at least one in-window indexed record: **{summary["three_source_any_overlap"]}** artists.',
                 f'- Meet BOTH OpenAlex and Getty trend thresholds: **{summary["three_source_both_signal_sufficient"]}** artists.',
                 '- This is a *coverage* check, not evidence that the two sources agree in shape.']
    lines+=['','## Interpretation guidance','',
            '1. Compare **coverage first**, not cluster-averaged event counts alone.',
            '2. Use `getty_event_prevalence_relative.png` for bin-level fraction with any recorded event, conditional on accepted Getty mappings.',
            '3. The event-count chart is outlier-sensitive; do not call it a market-wide transaction trend.',
            '4. Dated event coverage and identity-matching quality differ between historical periods and clusters. Cohort table is descriptive only.',
            '5. Use `coverage_without_name_warnings.csv` for an A2-name-risk check; it does not resolve Getty identity errors.',
            '6. `getty_case_candidates.csv` selects event-rich unflagged artists; it is NOT a random or centroid-representative sample.',
            '7. Do not fuse raw Getty counts, normalized Ngram rates, and OpenAlex article rates into one impact number.','',
            '## Figures','',
            '- `figures/getty_coverage_by_cluster.png`','- `figures/getty_event_prevalence_relative.png`',
            '- `figures/getty_event_counts_relative.png`']
    if overlap is not None:lines.append('- `figures/three_source_coverage.png`')
    (out/'REPORT_STAGE_C.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return out,summary


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a3-run',type=Path,required=True,help='A3 run directory')
    parser.add_argument('--getty-dir',type=Path,default=Path('data/getty/full'),help='FULL Getty directory, not pilot')
    parser.add_argument('--stage-b',type=Path,default=None,help='Optional Stage B cross_source_artist_summary.csv for three-source overlap')
    parser.add_argument('--output-root',type=Path,default=Path('results/ml/ngram_getty_validation'))
    parser.add_argument('--min-window-records',type=int,default=3)
    parser.add_argument('--min-nonzero-bins',type=int,default=2)
    args=parser.parse_args()
    output,result=run_analysis(a3_dir=args.a3_run,getty_dir=args.getty_dir,stage_b=args.stage_b,
                               output_root=args.output_root,min_window_records=args.min_window_records,
                               min_nonzero_bins=args.min_nonzero_bins)
    print(json.dumps({'output_directory':str(output), **{k:result[k] for k in [
        'a3_artists','a3_accepted_getty_mappings','a3_with_dated_event_in_window',
        'a3_with_getty_trend_signal','getty_full_dataset_events_without_year',
        'three_source_any_overlap','three_source_both_signal_sufficient']}},indent=2))

if __name__=='__main__':main()
