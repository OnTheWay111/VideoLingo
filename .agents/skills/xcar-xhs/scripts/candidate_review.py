"""候选入池门禁：审核结论绑定媒体及文案，脚本不伪造内容判断。"""
import datetime
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from content_policy import SCORE_RUBRIC

EDITORIAL_REVIEW_VERSION = 1
OPENING_ACTION_SECONDS = 3
ACTION_SERIES = 'car_mechanisms'

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
    editorial = (item.get('verification') or {}).get('editorial')
    if editorial is not None:
        values['editorial'] = editorial
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_editorial_review(item):
    editorial = (item.get('verification') or {}).get('editorial')
    if not isinstance(editorial, dict) or 'opening_action_at_sec' not in editorial:
        raise ValueError('选材审核缺少 editorial.opening_action_at_sec')
    at_sec = editorial['opening_action_at_sec']
    duration = (item.get('video') or {}).get('duration')
    if at_sec is not None and (
        type(at_sec) not in (int, float) or not math.isfinite(at_sec)
        or not 0 <= at_sec <= OPENING_ACTION_SECONDS or (duration and at_sec >= duration)
    ):
        raise ValueError('opening_action_at_sec 必须是前 3 秒内的有效时间点，或 null')
    for field in ('discussion_basis', 'follow_up_topic'):
        if not isinstance(editorial.get(field), str) or not editorial[field].strip():
            raise ValueError('选材审核缺少 editorial.' + field)
    return editorial


def candidate_sort_key(item):
    """先比较可见动作和讨论价值，再比较总分，兼容既有候选。"""
    editorial = (item.get('verification') or {}).get('editorial') or {}
    discussion_score = (item.get('score_breakdown') or {}).get('discussion_question', 0) if editorial else 0
    return (editorial.get('opening_action_at_sec') is None, -discussion_score, -float(item.get('score') or 0))


def validate_review(item, check_digest=True, require_rights=False):
    review = item.get('verification') or {}
    if review.get('status') != 'approved' or review.get('presentation') != 'car_only':
        raise ValueError('入池必须有 approved / car_only 视觉核验结论')
    fields = ('reviewer', 'reviewed_at', 'visual_evidence', 'first_three_seconds_evidence',
              'fact_evidence', 'series_evidence')
    if require_rights:
        fields += ('rights_evidence',)
    for field in fields:
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
    if require_rights and (source.scheme not in ('http', 'https') or not source.netloc):
        raise ValueError('rights_source 必须记录实际授权依据链接')
    reasons = review.get('score_reasons')
    if not isinstance(reasons, dict) or set(reasons) != set(SCORE_RUBRIC) or not all(
            isinstance(value, str) and value.strip() for value in reasons.values()):
        raise ValueError('五项评分必须逐项填写理由')
    if 'editorial' in review:
        editorial = validate_editorial_review(item)
        if item.get('topic_series_id') == ACTION_SERIES and editorial['opening_action_at_sec'] is None:
            raise ValueError('汽车机关图鉴须在前 3 秒看见关键动作')
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


def validate_publication_review(item):
    """已有候选兼容旧核验格式；发布阶段不重新要求转载授权字段。"""
    review = item.get('verification') or {}
    if review.get('status') in ('verified', 'passed'):
        if review.get('presentation') != 'car_only':
            raise ValueError('既有候选必须有 car_only 核验结论')
        frames = item.get('frames') or []
        if not frames or not all(Path(frame.get('file') or '/nonexistent').is_file() for frame in frames):
            raise ValueError('既有候选抽帧缺失，需重新核验')
        # 旧记录没有哈希时不伪造证明；已有哈希仍必须匹配。
        if review.get('video_sha256') and review['video_sha256'] != file_digest(item['video']['file']):
            raise ValueError('视频已变化，必须重新核验')
        if review.get('content_sha256') and review['content_sha256'] != content_digest(item):
            raise ValueError('文案已变化，必须重新核验')
        return review
    return validate_review(item, require_rights=False)
