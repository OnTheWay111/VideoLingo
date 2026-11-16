"""构造带明确审核证据的本地假素材，仅供离线测试。"""
import datetime
from pathlib import Path


def approve_item(item):
    from candidate_review import file_digest, content_digest
    from content_policy import SCORE_RUBRIC
    video = Path(item['video']['file'])
    frames = []
    for index, at_sec in enumerate((0.5, 12.0)):
        path = video.parent / ('review-%s-%d.jpg' % (item['candidate']['id'], index))
        path.write_bytes(b'fake frame')
        frames.append({'file': str(path), 'at_sec': at_sec})
    item['frames'] = frames
    item['verification'] = {
        'status': 'approved', 'presentation': 'car_only', 'reviewer': 'test reviewer',
        'reviewed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'visual_evidence': '测试车辆细节画面', 'first_three_seconds_evidence': '测试首帧运动机构',
        'fact_evidence': '测试来源支持事实', 'rights_evidence': '测试专用授权证据',
        'rights_source': 'https://example.com/license', 'series_evidence': '测试系列匹配',
        'score_reasons': {key: '测试评分依据' for key in SCORE_RUBRIC},
        'video_sha256': file_digest(video), 'content_sha256': content_digest(item),
    }
    return item
