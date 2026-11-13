import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.agents/skills/video-localize-zh/scripts/localize.py'
spec = importlib.util.spec_from_file_location('video_localize_zh', SCRIPT)
localize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(localize)


def make_video(directory, duration=4):
    path = directory / 'source.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=blue:s=320x240:r=24:d=%s' % duration,
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:d=%s' % duration,
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path),
    ], check=True, capture_output=True)
    return path


def make_request(tmp_path, mode='auto', transcript=None):
    video = make_video(tmp_path)
    request = {
        'source_id': 'source-1',
        'source_url': 'https://example.com/source',
        'video_file': str(video),
        'out_dir': str(tmp_path / 'out'),
        'presentation': 'car_only',
        'rights_status': 'official_source',
        'mode': mode,
        'asr': 'none',
        'sound_focus': True,
        'facts': [{'id': 'fact-1', 'text': '已核验的事实', 'source': 'https://example.com/fact'}],
    }
    if transcript is not None:
        request['transcript'] = transcript
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    return path


def write_script(request_path, segments, mode=None, disclosure='中文解说由 AI 辅助整理。'):
    out = Path(json.loads(request_path.read_text())['out_dir'])
    if mode is None:
        mode = json.loads((out / 'decision.json').read_text())['mode']
    script = {
        'mode': mode,
        'title': '测试标题',
        'body': '测试正文',
        'ai_disclosure': disclosure,
        'segments': segments,
    }
    (out / 'script_zh.json').write_text(json.dumps(script, ensure_ascii=False), encoding='utf-8')


def fake_tts(duration=1.5):
    def generate(text, path, voice, rate):
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'sine=frequency=660:sample_rate=48000:d=%s' % duration,
            '-c:a', 'libmp3lame', str(path),
        ], check=True, capture_output=True)
    return generate


def test_qwen_adapter_adds_project_root_to_module_path(monkeypatch):
    fake = types.SimpleNamespace(
        load_key=lambda key: 'en',
        transcribe_audio=lambda *args: {'language': 'en', 'segments': []},
    )
    monkeypatch.setitem(sys.modules, 'core.asr_backend.qwen_asr_local', fake)
    monkeypatch.setitem(sys.modules, 'core.asr_backend', types.SimpleNamespace(qwen_asr_local=fake))
    monkeypatch.setattr(localize.sys, 'path', [str(SCRIPT.parent)])
    result = localize.qwen_transcribe('audio.wav', 1, 'en')
    assert result['language'] == 'en'
    assert str(ROOT) in localize.sys.path


def test_auto_mode_requires_informational_speech(tmp_path):
    request = make_request(tmp_path, transcript=[{
        'start': 0, 'end': 2, 'text': 'This is a useful explanation',
    }])
    result = localize.analyze_request(request)
    assert result['decision']['mode'] == 'voiceover'
    assert result['analysis']['informational_speech'] is True


def test_auto_mode_ignores_short_interjection(tmp_path):
    request = make_request(tmp_path, transcript=[{'start': 0, 'end': 0.5, 'text': 'Oh.'}])
    result = localize.analyze_request(request)
    assert result['decision']['mode'] == 'caption_light'
    assert result['analysis']['speech_detected'] is True
    assert result['analysis']['informational_speech'] is False


def test_zero_duration_asr_interjection_does_not_abort(tmp_path):
    request = make_request(tmp_path, transcript=[{'start': 3, 'end': 3, 'text': 'Oh.'}])
    result = localize.analyze_request(request)
    assert result['decision']['mode'] == 'caption_light'
    transcript = localize.load_json(tmp_path / 'out/transcript.json')
    assert transcript['segments'][0]['end'] > transcript['segments'][0]['start']


def test_explicit_asr_failure_fallback_is_audited(tmp_path, monkeypatch):
    request_path = make_request(tmp_path)
    data = json.loads(request_path.read_text())
    data.update(asr='qwen', asr_failure='allow')
    request_path.write_text(json.dumps(data))
    monkeypatch.setattr(localize, 'qwen_transcribe', lambda *args: (_ for _ in ()).throw(ValueError('degenerate audio')))
    result = localize.analyze_request(request_path)
    assert result['analysis']['asr_error'] == 'degenerate audio'
    assert result['decision']['mode'] == 'caption_light'


def test_non_car_input_is_rejected(tmp_path):
    request_path = make_request(tmp_path)
    data = json.loads(request_path.read_text())
    data['presentation'] = 'talking_head_explainer'
    request_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='car_only'):
        localize.analyze_request(request_path)


def test_caption_render_and_visual_approval(tmp_path):
    request = make_request(tmp_path, mode='caption_light')
    localize.analyze_request(request)
    write_script(request, [
        {'start': 0, 'end': 2, 'subtitle': '中文字幕与事实', 'fact_refs': ['fact-1']},
        {'start': 2, 'end': 4, 'subtitle': '你喜欢哪一点？', 'kind': 'question'},
    ])
    result = localize.render_request(request, tts=fake_tts())
    assert result['qc']['status'] == 'needs_visual_review'
    assert Path(result['handoff']['video_file']).is_file()
    assert len(result['qc']['frames']) == 3
    review = tmp_path / 'review.json'
    review.write_text(json.dumps({
        'subtitle_clear': True,
        'key_visual_uncovered': True,
        'audio_clear': True,
        'source_sound_appropriate': True,
        'presentation': 'car_only',
    }))
    handoff = localize.approve_request(request, review)
    assert handoff['status'] == 'ready'
    assert localize.load_json(tmp_path / 'out/qc.json')['status'] == 'passed'


def test_voiceover_render_preserves_source_duration(tmp_path):
    request = make_request(tmp_path, mode='voiceover')
    localize.analyze_request(request)
    write_script(request, [{
        'start': 0, 'end': 4, 'subtitle': '中文配音与事实', 'narration': '中文配音与事实',
        'fact_refs': ['fact-1'],
    }], mode='voiceover')
    result = localize.render_request(request, tts=fake_tts())
    assert result['qc']['objective']['duration_match'] is True
    assert abs(result['qc']['localized_duration'] - 4) < 0.25
    assert len(result['narrations']) == 1


def test_script_requires_fact_reference(tmp_path):
    request = make_request(tmp_path, mode='caption_light')
    localize.analyze_request(request)
    write_script(request, [{'start': 0, 'end': 4, 'subtitle': '没有事实引用'}])
    with pytest.raises(ValueError, match='fact_refs'):
        localize.render_request(request)


def test_opening_must_contain_chinese_subtitle(tmp_path):
    request = make_request(tmp_path, mode='caption_light')
    localize.analyze_request(request)
    write_script(request, [
        {'start': 0, 'end': 3, 'subtitle': 'CENTODIECI', 'fact_refs': ['fact-1']},
        {'start': 3, 'end': 4, 'subtitle': '中文出现太晚', 'fact_refs': ['fact-1']},
    ])
    with pytest.raises(ValueError, match='前三秒'):
        localize.render_request(request)


def test_overlong_narration_is_rejected(tmp_path):
    request = make_request(tmp_path, mode='voiceover')
    localize.analyze_request(request)
    write_script(request, [
        {'start': 0, 'end': 1, 'subtitle': '短时间段', 'narration': '过长的配音', 'fact_refs': ['fact-1']},
        {'start': 1, 'end': 4, 'subtitle': '后续画面', 'fact_refs': ['fact-1']},
    ], mode='voiceover')
    with pytest.raises(ValueError, match='超过分配时长'):
        localize.render_request(request, tts=fake_tts(duration=2))


def test_approval_cannot_skip_failed_review(tmp_path):
    request = make_request(tmp_path, mode='caption_light')
    localize.analyze_request(request)
    write_script(request, [{'start': 0, 'end': 4, 'subtitle': '中文字幕', 'fact_refs': ['fact-1']}])
    localize.render_request(request)
    review = tmp_path / 'review.json'
    review.write_text(json.dumps({
        'subtitle_clear': False,
        'key_visual_uncovered': True,
        'audio_clear': True,
        'source_sound_appropriate': True,
        'presentation': 'car_only',
    }))
    with pytest.raises(ValueError, match='复核未通过'):
        localize.approve_request(request, review)
