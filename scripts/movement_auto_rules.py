"""Conservative, auditable rule labels for movement-name matches.

Labels are automated suggestions, not human judgments or calibrated probabilities.
"""
import re
import unicodedata

VERSION = 'movement-context-rules-1.0'
DISTINCTIVE = {'impressionism', 'post impressionism', 'cubism', 'fauvism',
               'surrealism', 'art nouveau', 'neoclassicism', 'abstract art'}
VISUAL = re.compile(r'\b(paintings?|painters?|watercolou?rs?|canvas|canvases|visual arts?|fine arts?|art histor\w*|sculptures?|sculptors?|galleries|gallery|museum|exhibitions?|pictorial|portrait\w*|landscape painting|oil painting)\b')
MOVEMENT_FRAME = re.compile(r'\b(artistic movement|art movement|artistic school|art school|avant garde|avantgarde|manifesto|artistic style)\b')
OTHER_MEDIA = re.compile(r'\b(cinema|cinematic|films?|theatre|theater|novels?|fiction|literary|literature|poetry|poets?|musical|music|opera|dance|dancing)\b')
GENERIC_SYMBOLISM = re.compile(r'\b(symbolism of|symbolism in|symbolism as|religious symbolism|animal symbolism|ritual symbolism|colour symbolism|color symbolism|sexual symbolism|sound symbolism|numerical symbolism)\b')
OTHER_REALISM = re.compile(r'\b(critical realism|scientific realism|metaphysical realism|moral realism|epistemological realism|philosophical realism|political realism|structural realism|legal realism|ontological realism|realism in international relations)\b')

def normalize(text):
    value=unicodedata.normalize('NFKD',text or '')
    value=''.join(c for c in value if not unicodedata.combining(c)).casefold()
    return ' '.join(re.findall(r'[^\W_]+',value))

def label(row):
    name=normalize(row['movement_name']);title=normalize(row.get('title',''));abstract=normalize(row.get('abstract',''))
    text=title+' '+abstract
    terms=[normalize(t.strip()) for t in row.get('matched_terms',row['movement_name']).split(';') if t.strip()]
    terms=terms or [name]
    pattern=lambda t: re.compile(r'(?<!\w)'+re.escape(t)+r'(?!\w)')
    title_hit=any(pattern(t).search(title) for t in terms)
    hits=[h for t in terms for h in pattern(t).finditer(text)]
    near=' '.join(text[max(0,h.start()-180):h.end()+180] for h in hits)
    visual=bool(VISUAL.search(near));frame=bool(MOVEMENT_FRAME.search(near))
    other_media=bool(OTHER_MEDIA.search(near));reason=[]
    if not hits:return result('uncertain','low','term_not_verified_in_export',title_hit,visual,other_media)
    # Substrings caused by punctuation normalization are not assigned to the
    # parent movement without independent evidence.
    if name=='impressionism' and re.search(r'\bpost impressionism\b',text):
        stripped=re.sub(r'\bpost impressionism\b',' ',text)
        if not pattern('impressionism').search(stripped):
            return result('uncertain','low','only_post_impressionism_context',title_hit,visual,other_media)
    if name=='futurism' and re.search(r'\b(afro futurism|afrofuturism)\b',text):
        stripped=re.sub(r'\b(afro futurism|afrofuturism)\b',' ',text)
        if not pattern('futurism').search(stripped):
            return result('uncertain','low','afrofuturism_not_confirmed_as_historical_futurism',title_hit,visual,other_media)
    if name=='dada' and re.search(r'\b(burman dada|dada burman|dada saheb|dada sahib)\b',title):
        if not frame and not visual:
            return result('irrelevant','high','title_dada_used_as_person_or_honorific',title_hit,visual,other_media)
    if name=='realism' and OTHER_REALISM.search(title):
        if not visual and not frame:
            return result('irrelevant','high','title_explicit_non_art_realism_sense',title_hit,visual,other_media)
        return result('uncertain','low','non_art_realism_sense_with_mixed_art_context',title_hit,visual,other_media)
    if name=='realism' and re.search(r'\b(socialist realism|poetic realism|magical realism|magic realism|neo realism|neorealism|new realism|photorealism)\b',near):
        return result('uncertain','low','related_realism_school_requires_scope_decision',title_hit,visual,other_media)
    if name=='symbolism':
        explicit=bool(re.search(r'\b(symbolist movement|symbolism movement|symbolist painting|symbolist painters?|symbolist art|french symbolism|belgian symbolism|russian symbolism)\b',near))
        if explicit and visual:
            return result('relevant','medium','explicit_symbolist_school_and_visual_art_context',title_hit,visual,other_media)
        return result('uncertain','low','symbolism_may_mean_generic_symbolic_content',title_hit,visual,other_media)
    if other_media and not visual:
        return result('uncertain','low','literary_performance_or_film_context_without_visual_art_evidence',title_hit,visual,other_media)
    if name in DISTINCTIVE and title_hit:
        return result('relevant','medium','distinctive_movement_name_in_title',title_hit,visual,other_media)
    if visual and (name in DISTINCTIVE or frame):
        return result('relevant','medium','movement_term_near_visual_art_evidence',title_hit,visual,other_media)
    return result('uncertain','low','insufficient_evidence_for_specific_painting_movement',title_hit,visual,other_media)

def result(label,confidence,reason,title_hit,visual,other_media):
    return {'automatic_label':label,'rule_strength':confidence,'automatic_reason':reason,
            'classification_method':VERSION,'term_in_title':int(title_hit),
            'nearby_visual_art_evidence':int(visual),'nearby_other_media_evidence':int(other_media)}
