// Names and selected garment types provide suggestions, never a fabric/condition diagnosis.
export const METADATA_CHOICES = {
  size: ['100','110','120','130','140','150','160','170'],
  style: ['편한 스타일','활동적인 스타일','단정한 스타일'],
  condition: ['좋음','보통','수선 필요'],
  garmentKind: ['티셔츠','셔츠','맨투맨','후드티','가디건','점퍼','바지','치마','원피스','기타'],
  sleeveLength: ['반팔','긴팔','민소매','해당 없음'],
  bottomLength: ['반바지','긴바지','해당 없음'],
  thickness: ['얇음','보통','두꺼움'], stretch: ['없음','약간','좋음'],
  pattern: ['무지','줄무늬','체크','프린트','기타']
};
export const METADATA_LABELS = {size:'이름표 사이즈',style:'스타일',condition:'옷 상태',garmentKind:'세부 종류',sleeveLength:'소매 길이',bottomLength:'바지 길이',thickness:'두께',stretch:'신축성',pattern:'무늬'};
export const SEASONS = ['봄','여름','가을','겨울'];

export function suggestGarmentMetadata({name='',garmentTypeId,category}={}) {
  const text=String(name);
  const values={}, sources={};
  const set=(key,value,source='name-rule')=>{values[key]=value;sources[key]=source;};
  const size=text.match(/(?:사이즈|size|호칭)\s*(100|110|120|130|140|150|160|170)(?!\d)/i);
  if(size)set('size',size[1]);
  const types=[[/원피스/,'원피스'],[/후드|후디/,'후드티'],[/맨투맨|스웨트셔츠/,'맨투맨'],[/가디건/,'가디건'],[/점퍼|재킷/,'점퍼'],[/티셔츠|크루넥/,'티셔츠'],[/셔츠/,'셔츠'],[/바지|팬츠|데님|레그 진/,'바지'],[/치마|스커트/,'치마']];
  const kind=types.find(([pattern])=>pattern.test(text));
  if(kind)set('garmentKind',kind[1]);
  else if(Number(garmentTypeId)===5)set('garmentKind','바지','type-rule');
  else if(category==='skirt'||Number(garmentTypeId)===6)set('garmentKind','치마','type-rule');
  else if(Number(garmentTypeId)===4)set('garmentKind','점퍼','type-rule');
  else if(Number(garmentTypeId)===3)set('garmentKind','원피스','type-rule');
  else if(Number(garmentTypeId)===2)set('garmentKind','티셔츠','type-rule');
  if(category==='pants'||Number(garmentTypeId)===5){
    set('sleeveLength','해당 없음','type-rule');
    if(/반바지|쇼트|하프/.test(text))set('bottomLength','반바지');
    else if(/긴바지/.test(text))set('bottomLength','긴바지');
  } else {
    set('bottomLength','해당 없음','type-rule');
    if(/반팔/.test(text))set('sleeveLength','반팔');
    else if(/긴팔/.test(text))set('sleeveLength','긴팔');
    else if(/민소매/.test(text))set('sleeveLength','민소매');
  }
  if(/스트라이프|줄무늬/.test(text))set('pattern','줄무늬');
  else if(/체크/.test(text))set('pattern','체크');
  else if(/프린트|그래픽/.test(text))set('pattern','프린트');
  else if(/무지/.test(text))set('pattern','무지');
  const seasons=SEASONS.filter(season=>new RegExp(`(^|[\\s/·,(])${season}(?=$|[\\s/·,)]|용|옷)`).test(text));
  if(/(^|[\s/·,(])(봄가을|춘추|간절기)/.test(text))for(const season of ['봄','가을'])if(!seasons.includes(season))seasons.push(season);
  if(seasons.length)set('seasons',seasons);
  return {values,sources};
}

export function createGarmentMetadataFields(container, initial={}) {
  let source;
  const seasonGroup=document.createElement('fieldset');
  const legend=document.createElement('legend');legend.textContent='입기 좋은 계절 (여러 개 선택 가능)';seasonGroup.append(legend);
  const inputs=SEASONS.map(season=>{
    const label=document.createElement('label'),input=document.createElement('input');
    input.type='checkbox';input.name='seasons';input.value=season;
    input.addEventListener('change',()=>{source='manual';});
    label.append(input,season);seasonGroup.append(label);return input;
  });
  container.append(seasonGroup);
  function reset(record={}) {
    const seasons=Array.isArray(record.seasons)?record.seasons:[record.season].filter(Boolean);
    inputs.forEach(input=>{input.checked=seasons.includes(input.value);});
    source=record.metadataSources?.seasons || (seasons.length?'saved':undefined);
  }
  reset(initial);
  return {
    reset,
    read(){return {seasons:inputs.filter(input=>input.checked).map(input=>input.value),metadataSources:source&&source!=='saved'?{seasons:source}:{}};},
    suggest(context){
      if(source==='manual'||source==='saved'||source==='demo')return;
      const seasons=suggestGarmentMetadata(context).values.seasons||[];
      inputs.forEach(input=>{input.checked=seasons.includes(input.value);});
      source=seasons.length?'name-rule':undefined;
    }
  };
}
