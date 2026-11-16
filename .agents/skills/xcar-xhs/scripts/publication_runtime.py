"""发布执行互斥及可恢复的提交日志，不保存浏览器凭据。"""
import contextlib
import datetime
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def execution_lock(history_path, profile='Default'):
    """同时保护 Chrome Profile 和共享历史；旧入口使用相同锁。"""
    if fcntl is None:
        raise RuntimeError('当前平台不支持发布执行文件锁；发布仅支持 macOS Chrome')
    identity = hashlib.sha256(profile.encode()).hexdigest()[:20]
    paths = sorted({
        Path(tempfile.gettempdir()) / ('xcar-xhs-%s-%s.lock' % (os.getuid(), identity)),
        Path(history_path).resolve().with_suffix('.execution.lock'),
    }, key=str)
    with contextlib.ExitStack() as stack:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open('a+'))
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('另一个小红书发布进程正在执行，请稍后重试') from exc
        yield


class SubmissionJournal:
    def __init__(self, history_path):
        self.path = Path(history_path).resolve().with_suffix('.submission.json')

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def write(self, status, plan, out_dir, **extra):
        value = {'status': status, 'plan': plan, 'out_dir': str(Path(out_dir).resolve()),
                 'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), **extra}
        atomic_json(self.path, value)
        return value
