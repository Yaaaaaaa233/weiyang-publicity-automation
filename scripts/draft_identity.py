"""Conservative candidate exclusions and explicit, evidence-backed identity checks."""
import re


def normalized(text):
    return re.sub(r'[\W_]+', '', text, flags=re.UNICODE)


def week(text):
    return set(re.findall(r'第([一二三四五六七八九十百零两\d]+)周', text))


def genre(text):
    if any(w in text for w in ('回顾', '总结', '圆满收官')):
        return 'retrospective'
    if any(w in text for w in ('预告', '报名', '招募', '招新')):
        return 'announcement'
    return None


def conflicts(article, card):
    """Exclude explicit contradictions, never infer identity from word overlap."""
    a, b = article['title'], card['title']
    reasons = []
    if week(a) and week(b) and week(a) != week(b):
        reasons.append('different_week')
    if genre(a) and genre(b) and genre(a) != genre(b):
        reasons.append('different_article_type')
    groups = ('体育', '学习', '生活', '实践', '文艺', '科协')
    expected = {g for g in groups if g in article.get('unit', '')}
    # Only inspect explicit title category before a delimiter, not incidental body words.
    prefix = re.split(r'[丨|｜：:]', b, maxsplit=1)[0]
    actual = {g for g in groups if g in prefix}
    if expected and actual and not expected.intersection(actual):
        reasons.append('different_department')
    return reasons


def validate_identity(item, candidates):
    """Agent supplies current body observations; similarity/reason alone cannot pass."""
    checks = item.get('body_checks', {})
    required = ('topic', 'department', 'date', 'week', 'unique_version')
    if any(k not in checks for k in required):
        raise ValueError('Identity confirmation requires topic, department, date, week and version body checks')
    for key in required:
        check = checks[key]
        allowed = ('matched', 'not_applicable') if key == 'week' else ('matched',)
        if not isinstance(check, dict) or check.get('result') not in allowed:
            raise ValueError('Identity check failed: ' + key)
        if not isinstance(check.get('reason'), str) or not check['reason'].strip():
            raise ValueError('Identity check needs observed facts: ' + key)
        if not check.get('evidence_paths') or not set(check['evidence_paths']).issubset(item.get('evidence_paths', [])):
            raise ValueError('Identity check needs referenced body evidence: ' + key)
    if len(candidates) > 1:
        reviews = item.get('candidate_reviews', [])
        if len(reviews) != len(candidates) or {r.get('id') for r in reviews} != {c['id'] for c in candidates}:
            raise ValueError('Review every candidate before resolving versions')
        for review in reviews:
            if review.get('result') not in ('same_manuscript', 'different_manuscript') or not review.get('reason'):
                raise ValueError('Each candidate needs an evidenced identity result')
            if not review.get('evidence_paths') or not set(review['evidence_paths']).issubset(item['evidence_paths']):
                raise ValueError('Candidate comparison needs referenced evidence')
        selected = next(r for r in reviews if r['id'] == item['id'])
        if selected['result'] != 'same_manuscript':
            raise ValueError('Selected candidate has not been confirmed')
        if sum(r['result'] == 'same_manuscript' for r in reviews) > 1:
            choice = item.get('version_choice', {})
            if choice.get('id') != item['id'] or not choice.get('reason') or not choice.get('evidence_paths'):
                raise ValueError('Multiple true versions require an explicit evidenced choice')
            if not set(choice['evidence_paths']).issubset(item['evidence_paths']):
                raise ValueError('Version choice evidence missing')
