"""Validate descriptive recommendation fields without inferring physical properties."""
CHOICES = {
    'size': {'100','110','120','130','140','150','160','170'},
    'style': {'편한 스타일','활동적인 스타일','단정한 스타일'},
    'condition': {'좋음','보통','수선 필요'},
    'garmentKind': {'티셔츠','셔츠','맨투맨','후드티','가디건','점퍼','바지','치마','원피스','기타'},
    'sleeveLength': {'반팔','긴팔','민소매','해당 없음'},
    'bottomLength': {'반바지','긴바지','해당 없음'},
    'thickness': {'얇음','보통','두꺼움'}, 'stretch': {'없음','약간','좋음'},
    'pattern': {'무지','줄무늬','체크','프린트','기타'},
}
SEASONS = {'봄','여름','가을','겨울'}
SOURCES = {'manual','name-rule','type-rule','demo','measurement-rule'}


def clean_metadata(payload, current=None):
    result = {}
    category = payload.get('category', (current or {}).get('category'))
    changed_category = bool(current) and 'category' in payload and category != current.get('category')
    intrinsic = {'pants': {'garmentKind':'바지','sleeveLength':'해당 없음'}, 'dress': {'garmentKind':'원피스','bottomLength':'해당 없음'}, 'top': {'bottomLength':'해당 없음'}}.get(category, {})
    derived = {}
    for key,value in intrinsic.items():
        if key not in payload and (changed_category or not (current or {}).get(key)):
            result[key]=value;derived[key]='type-rule'
    if changed_category:
        for key in ('garmentKind','sleeveLength','bottomLength'):
            if key not in payload and key not in intrinsic:
                result[key]='';derived[key]='type-rule'
    for key, choices in CHOICES.items():
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or value.strip() not in choices | {''}:
            raise ValueError('추천 정보의 사이즈·스타일·상태·세부 특성을 다시 선택해 주세요.')
        result[key] = value.strip()
    if 'seasons' in payload or 'season' in payload:
        seasons = payload.get('seasons', [payload['season']] if payload.get('season') else [])
        if not isinstance(seasons, list) or len(seasons) > 4 or any(not isinstance(s, str) or s not in SEASONS for s in seasons) or len(set(seasons)) != len(seasons):
            raise ValueError('계절은 봄·여름·가을·겨울 중에서 선택해 주세요.')
        result['seasons'] = list(seasons)
        result['season'] = seasons[0] if seasons else ''
    old_sources = (current or {}).get('metadataSources', {})
    sources = dict(old_sources) if isinstance(old_sources, dict) else {}
    submitted = payload.get('metadataSources', {})
    if not isinstance(submitted, dict) or any(key not in set(CHOICES) | {'seasons'} or not isinstance(value, str) or value not in SOURCES for key, value in submitted.items()):
        raise ValueError('추천 정보의 입력 근거를 확인해 주세요.')
    for key in result:
        if key != 'season':
            sources[key] = submitted.get(key, derived.get(key, 'manual'))
    if result:
        result['metadataSources'] = sources
    kind = result.get('garmentKind', '')
    if kind and (category == 'pants' and kind not in {'바지','기타'} or category == 'top' and kind in {'바지','치마','원피스'} or category == 'dress' and kind not in {'원피스','기타'}):
        raise ValueError('옷 종류와 세부 종류가 서로 맞지 않아요.')
    if category == 'pants' and result.get('sleeveLength', '') not in {'','해당 없음'} or category in {'top','dress'} and result.get('bottomLength', '') not in {'','해당 없음'}:
        raise ValueError('상의·하의에 맞는 소매와 바지 길이를 골라 주세요.')
    return result
