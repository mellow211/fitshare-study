# FitShare 학생 팀 개발 규칙

## 1. 프로젝트 목표

기존 FitShare의 사용자 경험을 참고하여,
학생 3명이 각각 스마트 실측, 맞춤 추천, 참여 랭킹 모듈을 독립적으로 개발하고
GitHub Pull Request로 하나의 웹앱에 통합한다.

## 2. 기술 규칙

- HTML, CSS, Vanilla JavaScript만 사용한다.
- 새로운 프레임워크나 npm 패키지는 임의로 추가하지 않는다.
- ES Module import/export를 사용한다.
- 공통 코드는 `js/`, 개별 기능은 `modules/`에 둔다.
- `index.html`에 큰 기능 로직을 직접 작성하지 않는다.
- 사진과 실험 회차를 PC에 저장하기 위해 `server.py`에 Python 표준 라이브러리 서버/API를 둘 수 있다. 추가 pip/npm 패키지 없이 `server-data/`에 파일로 저장한다.
- `server-data/`는 기본적으로 제외한다. 사용자가 데모 자료 업로드를 명시적으로 허용하면 계정·세션·등록 사진을 포함해 승인된 자료를 올릴 수 있다.
- 프로젝트 검증용 샘플 데이터인 `measure_dataset/`은 코드와 테스트에 필요하므로 GitHub에 올릴 수 있다.

## 3. 파일 담당

- `modules/measurement.js`: 실측 담당 학생
- `modules/recommendation.js`: 추천 담당 학생
- `modules/ranking.js`: 랭킹 담당 학생
- `css/styles.css`, `js/app.js`, `js/state.js`: 공통 파일이므로 함부로 대규모 수정하지 않는다.

## 4. 협업 규칙

- `main` 브랜치에서 직접 개발하지 않는다.
- 각자 feature 브랜치를 만든다.
- 다른 학생의 모듈 파일을 임의로 수정하지 않는다.
- 공통 파일 수정이 필요하면 이유를 먼저 설명하고 최소 변경한다.
- 한 번에 하나의 기능만 구현한다.
- 기능이 보이면 바로 테스트하고 commit 한다.

## 5. AI 사용 규칙

AI는 학생이 먼저 제시한 로직을 코드로 구현하는 도우미다.
AI가 임의로 핵심 로직을 바꾸지 않는다.

코드를 생성할 때 반드시:

1. 어떤 파일을 수정하는지 먼저 말한다.
2. 변경 이유를 쉬운 말로 설명한다.
3. 수정할 코드 전체를 보여준다.
4. 정상/경계/오류 테스트를 최소 3개 제시한다.
5. 다른 모듈에 영향이 있는지 확인한다.

## 6. 구현 규칙

- 먼저 가장 단순하게 동작하는 버전을 만든다.
- 화면에 결과가 보이도록 한다.
- 입력값 검증을 한다.
- 오류가 나면 사용자에게 이해 가능한 메시지를 보여준다.
- `console.log`는 학습용으로 사용 가능하지만 최종 버전에서는 불필요한 로그를 줄인다.

## 7. 완료 조건

기능 완료라고 말하려면:

- 로컬 웹페이지에서 직접 동작한다.
- 정상값 테스트를 통과한다.
- 경계값 테스트를 통과한다.
- 잘못된 입력에서도 앱이 깨지지 않는다.
- 학생이 자기 말로 로직을 설명할 수 있다.
- `git diff`를 확인한 뒤 commit/push 한다.

## 8. Superlite 스킬 사용 규칙

- 전역 Codex 플러그인 `superlite@superlite-dev`를 사용한다. 스킬은 `%CODEX_HOME%\plugins\cache\superlite-dev\superlite\<version>\skills`에서 확인한다.
- 작업을 시작할 때 이 요청에 맞는 Superlite `SKILL.md`와 `using-superlite\references\codex-tools.md`를 먼저 확인한다.
- Superpowers는 기본 워크플로로 사용하지 않는다. Superlite가 없거나 실행되지 않으면 임의로 Superpowers로 대체하지 말고 이유를 알린다.
- Superlite 절차를 따르되, 이 문서(AGENTS.md)의 프로젝트 규칙이 항상 우선이다.
- 이 프로젝트에서는 과한 자동화보다 "학생이 제시한 로직을 구현" 원칙을 우선한다.
- 스킬 적용 후에도 변경 범위는 최소화하고, 담당 모듈 경계를 지킨다.

## 9. 다음 세션 인계

- 작업을 시작하기 전에 `Handspff/진행상황.md`를 읽어 현재 진행 상태와 남은 일을 확인한다.
- 기능이나 계획을 바꾸면 같은 파일에 완료한 일, 아직 안 된 일, 시험 결과, 다음 행동을 쉬운 말로 기록한다.
- 확인하지 않은 기능은 완료했다고 적지 않는다.

## 10. 이미지 분할(세그멘테이션) 규칙

- 옷 등록의 실측: 너비는 GrabCut, 기장은 YOLO→GrabCut으로 마스크를 만든다. (`tools/vision_pipeline.py`의 `measure_garment`, 기장용은 `get_yolo_grabcut_provider`)
- 옷장 `옷만 보기` 이미지와 AI 상품 이미지 생성은 같은 YOLO + SAM 분할을 쓴다. YOLO가 옷 영역을 찾고 SAM 후보 마스크 중 YOLO와 겹치는 영역을 비교해 옷을 투명 배경으로 분리한다. (`tools/garment_segmentation.py`의 `make_product_cutout`; 결과 버전은 `yolo-sam-vit-b-v3`)
  - 목록의 `/thumbnail`은 저장된 상품 사진·옷장 미리보기·축소 원본 순서로 표시하며 AI 분리를 시작하지 않는다. 이전 버전 미리보기도 사용할 수 있지만 분할 버전 기록은 변경하지 않는다. 실측·명시적 옷 분리·가상 입어보기의 알고리즘은 유지한다.
  - Vercel에는 PyTorch가 들어가지 않아, 같은 YOLO 상자를 Replicate의 SAM(`tools/replicate_sam.py`)에 보내 분리한다. 로컬 `server.py`는 기존처럼 PC의 SAM을 쓴다.
- 두 용도의 분할 방식을 서로 섞지 않는다. 바꿔야 하면 먼저 이유를 설명하고 이 규칙도 함께 고친다.
- 가상 입어보기 표시용 분할은 원본 사진의 YOLO 마스크와 가장자리 연결 배경을 함께 사용하고, 복잡한 배경에서는 소매 보호 씨앗 GrabCut으로 보정한다. 이 코드는 `tools/tryon_segmentation.py`에 분리하며 등록 실측·AI 상품 사진의 알고리즘과 저장 결과를 변경하지 않는다.
