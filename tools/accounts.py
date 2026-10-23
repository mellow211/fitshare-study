"""아이디·비밀번호 계정과 로그인 기록(세션)을 파일에 저장합니다. 파이썬 표준 라이브러리만 씁니다.

- 비밀번호는 그대로 저장하지 않고, 사람마다 다른 salt 를 섞어 PBKDF2-SHA256 으로 한 번 더 섞은 값만 저장해요.
- 로그인 표(토큰)도 그대로 저장하지 않고 SHA-256 값만 저장해서, 파일이 새어도 바로 쓸 수 없어요.
- 같은 아이디로 비밀번호를 5번 틀리면 1분 동안 로그인을 막아요.
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

# 데모라서 글자 수·글자 종류 제한을 없앴어요. 비어 있지만 않으면 되고, 너무 긴 입력만 막는 안전 상한(200글자)이 있어요.
TEXT_MAX = 200
MAX_ACCOUNTS = 1000
ITERATIONS = 200_000
SESSION_SECONDS = 30 * 24 * 60 * 60
LOCK_AFTER_FAILS = 5
LOCK_SECONDS = 60


class AuthError(ValueError):
    """사용자에게 그대로 보여 줘도 되는 안내 문구를 담은 오류입니다."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS).hex()


def _token_key(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _read(path, default):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, type(default)) else default
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class AccountStore:
    def __init__(self, directory, clock=time.time):
        self.accounts_path = Path(directory) / 'accounts.json'
        self.sessions_path = Path(directory) / 'sessions.json'
        self.teacher_code_path = Path(directory) / 'teacher-code.json'
        self.clock = clock
        self.lock = threading.Lock()
        self.failures = {}  # 아이디 -> (틀린 횟수, 막힌 시각)

    def _teacher_code_record(self):
        record = _read(self.teacher_code_path, {})
        if isinstance(record.get('salt'), str) and isinstance(record.get('hash'), str):
            return record
        if self.teacher_code_path.exists():
            raise AuthError('교사 인증 설정을 읽지 못했어요. 서버 관리자에게 확인해 주세요.', 503)
        salt = secrets.token_bytes(16)
        record = {'salt': salt.hex(), 'hash': _hash_password('fitshare', salt)}
        _write(self.teacher_code_path, record)
        return record

    def _teacher_code_matches(self, candidate):
        record = self._teacher_code_record()
        salt = bytes.fromhex(record['salt'])
        return hmac.compare_digest(_hash_password(candidate, salt), record['hash'])

    def clean_username(self, username):
        value = username.strip().lower() if isinstance(username, str) else ''
        if not value:
            raise AuthError('아이디를 입력해 주세요.')
        if len(value) > TEXT_MAX:
            raise AuthError(f'아이디는 {TEXT_MAX}글자까지 쓸 수 있어요.')
        return value

    def clean_nickname(self, nickname):
        value = re.sub(r'\s+', ' ', nickname).strip() if isinstance(nickname, str) else ''
        if not value:
            raise AuthError('이름(별명)을 입력해 주세요.')
        if len(value) > TEXT_MAX:
            raise AuthError(f'이름(별명)은 {TEXT_MAX}글자까지 쓸 수 있어요.')
        return value

    def signup(self, username, password, nickname, teacher_code=None):
        username = self.clean_username(username)
        nickname = self.clean_nickname(nickname)
        if not isinstance(password, str) or not password:
            raise AuthError('비밀번호를 입력해 주세요.')
        if len(password) > TEXT_MAX:
            raise AuthError(f'비밀번호는 {TEXT_MAX}글자까지 쓸 수 있어요.')
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        with self.lock:
            role = 'student'
            if teacher_code is not None:
                if not isinstance(teacher_code, str) or not teacher_code.strip() or len(teacher_code) > TEXT_MAX:
                    raise AuthError('교사 인증 코드를 확인해 주세요.')
                teacher_code = teacher_code.strip()
                if not self._teacher_code_matches(teacher_code):
                    raise AuthError('교사 인증 코드가 맞지 않아요.')
                role = 'teacher'
            accounts = _read(self.accounts_path, {})
            if username in accounts:
                raise AuthError('이미 있는 아이디예요. 다른 아이디를 써 주세요.', 409)
            if any(item.get('name', '').lower() == nickname.lower() for item in accounts.values()):
                raise AuthError('이미 있는 이름(별명)이에요. 다른 이름을 써 주세요.', 409)
            if len(accounts) >= MAX_ACCOUNTS:
                raise AuthError('가입할 수 있는 사람 수가 가득 찼어요.', 409)
            accounts[username] = {'name': nickname, 'salt': salt.hex(), 'hash': password_hash,
                                  'createdAt': int(self.clock()), 'role': role}
            _write(self.accounts_path, accounts)
        return {'username': username, 'name': nickname, 'role': role}

    def login(self, username, password):
        # 틀린 이유(아이디/비밀번호)를 알려 주지 않아서 아이디 존재 여부를 추측하기 어렵게 해요.
        wrong = AuthError('아이디 또는 비밀번호가 맞지 않아요.', 401)
        username = username.strip().lower() if isinstance(username, str) else ''
        if not isinstance(password, str) or len(password) > TEXT_MAX:
            raise wrong
        now = self.clock()
        with self.lock:
            locked_until = self.failures.get(username, (0, 0))[1]
            if locked_until > now:
                raise AuthError('비밀번호를 여러 번 틀렸어요. 1분 뒤에 다시 해 보세요.', 429)
            account = _read(self.accounts_path, {}).get(username)
        salt = bytes.fromhex(account['salt']) if account else b'\0' * 16
        candidate = _hash_password(password, salt)
        ok = bool(account) and hmac.compare_digest(candidate, account['hash'])
        with self.lock:
            if not ok:
                fails = self.failures.get(username, (0, 0))[0] + 1
                self.failures[username] = (0, now + LOCK_SECONDS) if fails >= LOCK_AFTER_FAILS else (fails, 0)
                raise wrong
            self.failures.pop(username, None)
        return {'username': username, 'name': account['name'], 'role': account.get('role', 'student')}

    def create_session(self, username):
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.lock:
            sessions = {key: item for key, item in _read(self.sessions_path, {}).items() if item.get('expires', 0) > now}
            sessions[_token_key(token)] = {'username': username, 'expires': now + SESSION_SECONDS}
            _write(self.sessions_path, sessions)
        return token

    def user_for_token(self, token):
        if not isinstance(token, str) or not token:
            return None
        with self.lock:
            session = _read(self.sessions_path, {}).get(_token_key(token))
            if not session or session.get('expires', 0) <= self.clock():
                return None
            account = _read(self.accounts_path, {}).get(session.get('username'))
        return {'username': session['username'], 'name': account['name'], 'role': account.get('role', 'student')} if account else None

    def change_teacher_code(self, actor_username, new_code):
        actor = actor_username.strip().lower() if isinstance(actor_username, str) else ''
        if not isinstance(new_code, str) or not new_code.strip() or len(new_code) > TEXT_MAX:
            raise AuthError('새 교사 인증 코드를 1~200글자로 입력해 주세요.')
        with self.lock:
            accounts = _read(self.accounts_path, {})
            if accounts.get(actor, {}).get('role', 'student') != 'teacher':
                raise AuthError('교사만 인증 코드를 바꿀 수 있어요.', 403)
            salt = secrets.token_bytes(16)
            _write(self.teacher_code_path, {'salt': salt.hex(), 'hash': _hash_password(new_code.strip(), salt)})

    def reset_student_password(self, actor_username, student_username, new_password):
        actor = actor_username.strip().lower() if isinstance(actor_username, str) else ''
        target = student_username.strip().lower() if isinstance(student_username, str) else ''
        if not isinstance(new_password, str) or not new_password.strip() or len(new_password) > TEXT_MAX:
            raise AuthError('새 비밀번호를 1~200글자로 입력해 주세요.')
        with self.lock:
            accounts = _read(self.accounts_path, {})
            actor_record = accounts.get(actor)
            target_record = accounts.get(target)
            if not actor_record or actor_record.get('role', 'student') != 'teacher':
                raise AuthError('교사만 학생 비밀번호를 바꿀 수 있어요.', 403)
            if not target_record or target_record.get('role', 'student') != 'student':
                raise AuthError('비밀번호를 바꿀 학생 아이디를 찾지 못했어요.', 404)
            salt = secrets.token_bytes(16)
            target_record['salt'] = salt.hex()
            target_record['hash'] = _hash_password(new_password, salt)
            accounts[target] = target_record
            sessions = _read(self.sessions_path, {})
            active = {key: item for key, item in sessions.items() if item.get('username') != target}
            if len(active) != len(sessions):
                _write(self.sessions_path, active)
            # If persisting the password fails, the old password still works,
            # but every existing session has already been revoked.
            _write(self.accounts_path, accounts)

    def change_own_password(self, username, current_password, new_password):
        actor = username.strip().lower() if isinstance(username, str) else ''
        if not isinstance(new_password, str) or not new_password.strip() or len(new_password) > TEXT_MAX:
            raise AuthError('새 비밀번호를 1~200글자로 입력해 주세요.')
        if not isinstance(current_password, str) or len(current_password) > TEXT_MAX:
            raise AuthError('현재 비밀번호가 맞지 않아요.', 401)
        with self.lock:
            accounts = _read(self.accounts_path, {})
            account = accounts.get(actor)
            if not account:
                raise AuthError('현재 비밀번호가 맞지 않아요.', 401)
            current_hash = _hash_password(current_password, bytes.fromhex(account['salt']))
            if not hmac.compare_digest(current_hash, account['hash']):
                raise AuthError('현재 비밀번호가 맞지 않아요.', 401)

            salt = secrets.token_bytes(16)
            account['salt'] = salt.hex()
            account['hash'] = _hash_password(new_password, salt)
            accounts[actor] = account
            sessions = _read(self.sessions_path, {})
            active = {key: item for key, item in sessions.items() if item.get('username') != actor}
            if len(active) != len(sessions):
                _write(self.sessions_path, active)
            _write(self.accounts_path, accounts)

    def destroy_session(self, token):
        if not isinstance(token, str) or not token:
            return
        with self.lock:
            sessions = _read(self.sessions_path, {})
            if sessions.pop(_token_key(token), None) is not None:
                _write(self.sessions_path, sessions)
