"""Fill missing metadata and estimate only sizes backed by available measurements."""
import json
from pathlib import Path
from tools.garment_metadata import CHOICES
from tools.garment_size_estimation import estimate_korean_size

ROOT = Path(__file__).resolve().parents[1]


def complete(item):
    result = dict(item)
    sources = dict(item.get('metadataSources') or {})
    for key in CHOICES:
        result.setdefault(key, '')
    size_category = 'shorts' if item.get('category') == 'pants' and item.get('bottomLength') == '반바지' else item.get('category')
    if not result['size'] and size_category in {'top', 'dress', 'pants', 'skirt', 'shorts'}:
        try:
            result['size'] = estimate_korean_size(size_category, item.get('widthCm'), item.get('lengthCm'))
        except (KeyError, ValueError):
            pass
        else:
            sources['size'] = 'measurement-rule'
    result.setdefault('seasons', [item['season']] if item.get('season') in {'봄','여름','가을','겨울'} else [])
    name = item.get('name', '')
    kind = '바지' if item.get('category')=='pants' else '원피스' if item.get('category')=='dress' else '점퍼' if item.get('garmentTypeId')==4 else '티셔츠' if '크롭티' in name or '티셔츠' in name else ''
    if not result['garmentKind'] and kind:
        result['garmentKind']=kind
        sources['garmentKind']='name-rule' if kind=='티셔츠' else 'type-rule'
    if item.get('category')=='pants' and not result['sleeveLength']:
        result['sleeveLength']='해당 없음';sources['sleeveLength']='type-rule'
    if item.get('category') in {'top','dress'} and not result['bottomLength']:
        result['bottomLength']='해당 없음';sources['bottomLength']='type-rule'
    result['metadataSources']=sources
    return result


def main():
  for root in (ROOT/'server-data'/'garments', ROOT/'server-data'/'nanum-demo'/'garments'):
    if not (root/'index.json').is_file():
        continue
    path=root/'index.json'
    records=json.loads(path.read_text(encoding='utf-8'))
    for index,item in enumerate(records):
        if item.get('isDemo'):
            continue
        metadata_path=root/item['id']/'garment.json'
        original=json.loads(metadata_path.read_text(encoding='utf-8'))
        updated=complete(original)
        metadata_path.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        records[index]={**item,**{key:value for key,value in updated.items() if key!='photoUrl'}}
    path.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  print('Added conservative metadata and reference-based sizes to existing garments.')


if __name__=='__main__':
    main()
