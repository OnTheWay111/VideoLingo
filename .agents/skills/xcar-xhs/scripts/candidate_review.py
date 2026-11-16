"""候选入池门禁：审核结论绑定媒体及文案，脚本不伪造内容判断。"""
import hashlib
import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit

from content_policy import SCORE_RUBRIC

COPY_FIELDS = ('title', 'recommendation', 'brand', 'model', 'modification_project',
               'title_detail', 'verifiable_fact', 'fact_source', 'discussion_question',
               'topics', 'ai_disclosure', 'score_breakdown')


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def content_digest(item):
    values = {key: item.get(key) or ({} if key == 'score_breakdown' else [] if key == 'topics' else '')
              for key in COPY_FIELDS}
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_review(item, check_digest=True):
    review = item.get('verification') or {}
    if review.get('status') != 'approved' or review.get('presentation') != 'car_only':
        raise ValueError('入池必须有 approved / car_only 视觉核验结论')
    for field in ('reviewer', 'reviewed_at', 'visual_evidence', 'first_three_seconds_evidence',
                  'fact_evidence', 'rights_evidence', 'series_evidence'):
        if not isinstance(review.get(field), str) or not review[field].strip():
            raise ValueError('核验缺少依据：' + field)
    try:
        reviewed = datetime.datetime.fromisoformat(review['reviewed_at'].replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('reviewed_at 必须是 ISO 8601 时间') from exc
    if reviewed.tzinfo is None:
        raise ValueError('reviewed_at 必须包含时区')
    if reviewed > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
        raise ValueError('reviewed_at 不能是未来时间')
    source = urlsplit(str(review.get('rights_source') or ''))
    if source.scheme not in ('http', 'https') or not source.netloc:
        raise ValueError('rights_source 必须记录实际授权依据链接')
    reasons = review.get('score_reasons')
    if not isinstance(reasons, dict) or set(reasons) != set(SCORE_RUBRIC) or not all(
            isinstance(value, str) and value.strip() for value in reasons.values()):
        raise ValueError('五项评分必须逐项填写理由')
    frames = item.get('frames') or []
    if not any(0 <= frame.get('at_sec', -1) <= 3 and Path(frame['file']).is_file() for frame in frames):
        raise ValueError('缺少前三秒有效抽帧')
    if not any(frame.get('at_sec', -1) > 3 and Path(frame['file']).is_file() for frame in frames):
        duration = (item.get('video') or {}).get('duration') or 0
        if duration > 3:
            raise ValueError('缺少中后段有效抽帧')
    if check_digest:
        if review.get('video_sha256') != file_digest(item['video']['file']):
            raise ValueError('视频已变化，必须重新核验')
        if review.get('content_sha256') != content_digest(item):
            raise ValueError('文案已变化，必须重新核验')
    return review
