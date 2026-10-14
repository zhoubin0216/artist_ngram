"""Screen ambiguous movement-name matches using saved topics and text context.

Rule-based screening, not human review or a semantic-model judgment.
Place under scripts/ and run from the artist_ngram repository root.
Original files are read-only. No API calls or full-text downloads.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'movement-context-topics-1.0'

def normalize(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(c for c in value if not unicodedata.combining(c)).casefold()
    return ' '.join(re.findall(r'[^\W_]+', value))

def wid(value):
    match = re.fullmatch(r'(?:https?://openalex\.org/)?(W\d+)', (value or '').strip(), re.I)
    if not match:
        raise ValueError('Invalid Work ID: ' + str(value))
    return match.group(1).upper()

def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()

def contains(text, phrase):
    return bool(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text))

def evidence_windows(text, phrases, radius=180):
    windows = []
    for phrase in phrases:
        for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text):
            snippet = text[max(0, match.start()-radius):match.end()+radius]
            if snippet not in windows:
                windows.append(snippet)
    return windows

VISUAL = re.compile(r'\b(paintings?|painters?|painted|watercolou?rs?|canvas|canvases|'
                    r'pictorial|visual arts?|fine arts?|art histor\w*|sculptures?|sculptors?|'
                    r'portrait\w*|landscape painting|oil painting|printmaking|lithograph\w*)\b')
VISUAL_TOPICS = re.compile(r'\b(painting|painters?|visual arts?|fine arts?|art history|'
                           r'sculpture|printmaking|pictorial)\b')
OTHER_MEDIA = re.compile(r'\b(cinema|cinematic|films?|theatre|theater|novels?|fiction|'
                         r'literary|literature|poetry|poets?|musical|music|opera|dance)\b')
ART_FRAME = re.compile(r'\b(art movement|artistic movement|artistic school|artistic style|'
                       r'visual style|painting style|avant garde painting)\b')
OTHER_REALISM = re.compile(r'\b(critical realism|scientific realism|metaphysical realism|'
                          r'moral realism|epistemological realism|philosophical realism|'
                          r'political realism|structural realism|legal realism|ontological realism|'
                          r'realism in international relations)\b')
DISTINCTIVE = {'impressionism', 'post impressionism', 'cubism', 'fauvism', 'surrealism',
               'art nouveau', 'neoclassicism', 'abstract art'}

def classify(name, title, abstract, terms, primary_topic, topics):
    """Conservative screen: supported / other_sense / uncertain.

    Topic evidence supports a visual-art context but never establishes movement
    identity by itself. Missing visual evidence never means automatic exclusion.
    """
    movement = normalize(name)
    nt, na = normalize(title), normalize(abstract)
    term_list = [normalize(t) for t in terms if normalize(t)] or [movement]
    full = nt + ' ' + na
    windows = evidence_windows(full, term_list)
    near = ' | '.join(windows)
    title_hit = any(contains(nt, term) for term in term_list)
    visual_near = bool(VISUAL.search(near))
    visual_topic = bool(VISUAL_TOPICS.search(normalize(primary_topic + ' ' + ' '.join(topics))))
    other_media = bool(OTHER_MEDIA.search(near))
    framed = bool(ART_FRAME.search(near))
    # Evidence excerpt is the normalized source text, not a fabricated quote.
    result = {'context_label': 'uncertain', 'context_reason': '',
              'term_in_title': int(title_hit), 'visual_text_evidence': int(visual_near),
              'visual_topic_evidence': int(visual_topic), 'other_media_evidence': int(other_media),
              'evidence_excerpt': ' | '.join(windows[:3]), 'classification_method': VERSION}
    def done(label, reason):
        return {**result, 'context_label': label, 'context_reason': reason}
    if not windows:
        return done('uncertain', 'term_not_verified_in_available_title_or_abstract')
    if movement == 'impressionism':
        without = re.sub(r'\bpost impressionism\b', ' ', full)
        if not contains(without, 'impressionism'):
            return done('uncertain', 'only_post_impressionism_not_independent_impressionism')
    if movement == 'futurism':
        without = re.sub(r'\b(afro futurism|afrofuturism)\b', ' ', full)
        if not contains(without, 'futurism'):
            return done('uncertain', 'afrofuturism_scope_requires_decision')
    if movement == 'realism' and OTHER_REALISM.search(nt):
        if not visual_near and not visual_topic and not framed:
            return done('other_sense', 'explicit_non_art_realism_in_title')
        return done('uncertain', 'non_art_realism_and_visual_art_context_mixed')
    if movement == 'dada' and re.search(r'\b(burman dada|dada burman|dada saheb|dada sahib)\b', nt):
        if not framed and not visual_near:
            return done('other_sense', 'dada_as_person_or_honorific')
        return done('uncertain', 'person_name_and_art_context_mixed')
    if movement == 'symbolism':
        school = re.search(r'\b(symbolist movement|symbolism movement|symbolist painting|'
                           r'symbolist painters?|symbolist art|french symbolism|belgian symbolism)\b', full)
        if school and (visual_near or visual_topic):
            return done('supported', 'symbolist_school_language_and_visual_art_evidence')
        return done('uncertain', 'generic_symbolic_content_not_proof_of_symbolist_movement')
    if movement == 'realism' and re.search(r'\b(socialist realism|poetic realism|magic realism|'
                                         r'magical realism|neo realism|new realism)\b', near):
        return done('uncertain', 'related_realism_school_scope_requires_decision')
    if other_media and not visual_near:
        return done('uncertain', 'media_context_without_explicit_visual_art_connection')
    if movement in DISTINCTIVE and title_hit and not other_media:
        return done('supported', 'distinctive_movement_term_in_title')
    if movement in DISTINCTIVE and visual_near:
        return done('supported', 'distinctive_movement_term_near_visual_art_context')
    if framed and visual_near:
        return done('supported', 'explicit_art_movement_frame_with_visual_art_context')
    if movement in DISTINCTIVE and visual_topic and title_hit:
        return done('supported', 'title_movement_with_specific_visual_art_topic')
    return done('uncertain', 'insufficient_context_to_distinguish_target_movement')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, default=ROOT/'results/openalex_movement_triage/uncertain_direct_only.csv')
    ap.add_argument('--classification', type=Path, default=ROOT/'results/openalex_movement_classification/movement_automatic_classification.csv')
    ap.add_argument('--candidates', type=Path, default=ROOT/'data/openalex_movements/core/any/full/candidates')
    ap.add_argument('--output', type=Path, default=ROOT/'results/openalex_movement_context')
    ap.add_argument('--limit', type=int, help='Inspect only the first N input rows; output is explicitly partial')
    args = ap.parse_args()
    if args.limit is not None and args.limit < 1:
        ap.error('--limit must be positive')
    if args.output.exists() and any(args.output.iterdir()):
        ap.error('Output directory is not empty; choose a new --output directory to preserve previous results')
    try:
        selected = read_csv(args.input)
        if args.limit: selected = selected[:args.limit]
        wanted = {(r['movement_id'], wid(r['work_id'])) for r in selected}
        if len(wanted) != len(selected): raise ValueError('Duplicate movement-work input keys')
        if not selected: raise ValueError('No input records')
        source = {}
        for r in read_csv(args.classification):
            key = (r['movement_id'], wid(r['work_id']))
            if key in wanted:
                if key in source: raise ValueError('Duplicate classification key')
                if r.get('automatic_label') != 'uncertain':
                    raise ValueError('This command is restricted to uncertain records')
                source[key] = r
        if set(source) != wanted: raise ValueError('Input records missing from full classification table')
        archive = {}; fingerprints = {}; conflicts = set(); files = sorted(args.candidates.glob('*.jsonl'))
        if not files: raise ValueError('No candidate archives')
        used_files = []
        for path in files:
            used = False
            with path.open(encoding='utf-8') as f:
                for number, line in enumerate(f, 1):
                    if not line.strip(): continue
                    try: record = json.loads(line)
                    except ValueError as exc: raise ValueError(f'Invalid JSON {path}:{number}') from exc
                    work = record['work']; key = (record['movement_id'], wid(work.get('id')))
                    if key not in wanted: continue
                    used = True
                    fingerprint = json.dumps({k: work.get(k) for k in ['title', 'publication_year', 'abstract_inverted_index', 'primary_topic', 'topics']}, sort_keys=True)
                    if key in fingerprints and fingerprints[key] != fingerprint: conflicts.add(key)
                    else: archive[key] = work; fingerprints[key] = fingerprint
            if used: used_files.append({'file': path.name, 'sha256': sha(path)})
        outputs = []
        for key in sorted(wanted):
            r = source[key]; work = archive.get(key, {})
            primary = (work.get('primary_topic') or {}).get('display_name', '')
            topic_names = [t.get('display_name', '') for t in work.get('topics') or []]
            terms = [t.strip() for t in r.get('matched_terms', r['movement_name']).split(';') if t.strip()]
            decision = classify(r['movement_name'], r.get('title', ''), r.get('abstract', ''), terms, primary, topic_names)
            if key not in archive:
                decision.update(context_label='uncertain', context_reason='missing_candidate_archive')
            elif key in conflicts:
                decision.update(context_label='uncertain', context_reason='conflicting_candidate_snapshots')
            elif str(work.get('publication_year')) != str(r['publication_year']):
                decision.update(context_label='uncertain', context_reason='publication_year_snapshot_conflict')
            elif normalize(work.get('title', '')) != normalize(r.get('title', '')):
                decision.update(context_label='uncertain', context_reason='title_snapshot_conflict')
            outputs.append({'movement_id': key[0], 'movement_name': r['movement_name'], 'work_id': key[1],
                            'publication_year': r['publication_year'], 'title': r.get('title', ''),
                            'abstract': r.get('abstract', ''), 'matched_terms': r.get('matched_terms', ''),
                            'primary_topic': primary, 'topic_names': '; '.join(topic_names),
                            'previous_automatic_label': r.get('automatic_label', ''),
                            'manual_label': r.get('manual_label', ''), **decision})
        args.output.mkdir(parents=True, exist_ok=True)
        fields = list(outputs[0])
        write_csv(args.output/'context_classification.csv', fields, outputs)
        for label in ['supported', 'other_sense', 'uncertain']:
            write_csv(args.output/(label+'.csv'), fields, [r for r in outputs if r['context_label']==label])
        per_movement = []
        for mid in sorted({r['movement_id'] for r in outputs}):
            group = [r for r in outputs if r['movement_id']==mid]; counts=Counter(r['context_label'] for r in group)
            per_movement.append({'movement_id': mid, 'movement_name': group[0]['movement_name'],
                                 'total': len(group), **{k: counts[k] for k in ['supported','other_sense','uncertain']}})
        write_csv(args.output/'context_summary.csv', list(per_movement[0]), per_movement)
        summary = {'classification_method': VERSION, 'is_human_review': False,
                   'is_partial_run': args.limit is not None, 'rows': len(outputs),
                   'counts': dict(Counter(r['context_label'] for r in outputs)),
                   'reason_counts': dict(Counter(r['context_reason'] for r in outputs)),
                   'input_sha256': sha(args.input), 'classification_sha256': sha(args.classification),
                   'script_sha256': sha(Path(__file__)), 'candidate_files_used': used_files,
                   'limitations': ['Rules do not resolve all context or multilingual text.',
                                   'Topic labels alone do not establish movement identity.',
                                   'Supported labels are automated evidence, not measured accuracy or main-discussion status.',
                                   'Outputs are a screen and do not change earlier union counts.']}
        (args.output/'context_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps({'rows': summary['rows'], 'partial': summary['is_partial_run'], 'counts': summary['counts']}, indent=2))
        print('Output:', args.output)
    except (OSError, ValueError, KeyError) as exc:
        ap.exit(1, 'FAILED: '+str(exc)+'\n')

if __name__ == '__main__': main()
