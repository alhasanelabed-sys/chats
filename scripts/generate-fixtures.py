#!/usr/bin/env python3
"""Offline, seeded synthetic meeting audio. Requires ffmpeg and speech mode eSpeak NG.

Truth IDs describe generation, never recognition. tone-check deliberately emits no speech.
"""
import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_VOICES = {'ar': ('ar',), 'en': ('en-us', 'en'), 'he': ('he',), 'ru': ('ru',),
                   'el': ('el',), 'uk': ('uk',), 'zh': ('cmn', 'zh'), 'fa': ('fa',), 'ur': ('ur',)}
MAX_BYTES = 24_000_000


def run(args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        message = result.stderr.decode('utf-8', errors='replace')[-1500:]
        raise RuntimeError(f'{Path(args[0]).name} failed: {message}')
    return result.stdout


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def db(rms):
    return None if rms <= 0 else round(20 * math.log10(rms), 6)


def metrics(samples):
    if not samples:
        return {'rms_dbfs': None, 'peak_dbfs': None, 'clipped_samples': 0}
    power = sum(float(x) ** 2 for x in samples) / len(samples)
    peak = max(abs(x) for x in samples)
    return {'rms_dbfs': db(math.sqrt(power)), 'peak_dbfs': db(peak),
            'clipped_samples': sum(abs(x) >= 1 for x in samples)}


def normalized(samples, target_dbfs, peak_ceiling=None):
    rms = math.sqrt(sum(float(x) ** 2 for x in samples) / max(1, len(samples)))
    if rms == 0:
        return samples
    scale = 10 ** (target_dbfs / 20) / rms
    peak = max(abs(x) for x in samples)
    if peak_ceiling is not None and peak * scale > 10 ** (peak_ceiling / 20):
        scale = 10 ** (peak_ceiling / 20) / peak
    return array('f', (x * scale for x in samples))


def reference_clip(samples, rate):
    """Keep the authored utterance intact; pad silence to the server's 2..10s contract."""
    if len(samples) > 10 * rate:
        raise RuntimeError('Reference speech exceeds ten seconds; refusing to truncate it')
    result = array('f', samples)
    if len(result) < 2 * rate:
        result.extend(array('f', [0]) * (2 * rate - len(result)))
    return result


def read_wav(path):
    with wave.open(str(path), 'rb') as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise RuntimeError('Expected signed 16-bit mono WAV')
        pcm = array('h', wav.readframes(wav.getnframes()))
        if sys.byteorder != 'little':
            pcm.byteswap()
        return array('f', (x / 32768.0 for x in pcm)), wav.getframerate()


def write_wav(path, samples, rate):
    if any(abs(x) >= 1 for x in samples):
        raise RuntimeError('Mix would clip; refusing to write')
    pcm = array('h', (round(float(x) * 32767) for x in samples))
    if sys.byteorder != 'little':
        pcm.byteswap()
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())


def export_audio(base, samples, rate, formats, temporary, ffmpeg):
    temporary_wav = temporary / 'export.wav'
    write_wav(temporary_wav, samples, rate)
    paths = []
    if formats in ('wav', 'both'):
        wav_path = base.with_suffix('.wav')
        shutil.copyfile(temporary_wav, wav_path)
        paths.append(wav_path)
    if formats in ('m4a', 'both'):
        m4a_path = base.with_suffix('.m4a')
        run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(temporary_wav),
             '-map_metadata', '-1', '-ac', '1', '-ar', str(rate), '-c:a', 'aac', '-b:a', '64k',
             '-fflags', '+bitexact', '-flags:a', '+bitexact', '-movflags', '+faststart', str(m4a_path)])
        paths.append(m4a_path)
    for path in paths:
        if path.stat().st_size >= MAX_BYTES:
            raise RuntimeError(f'Generated file exceeds 24 MiB: {path.name}')
    return paths


def voices(engine):
    output = run([engine, '--voices']).decode('utf-8', errors='replace')
    found = set()
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[0].isdigit():
            found.add(fields[1])
    return found


def voice_for(language, available):
    return next((voice for voice in LANGUAGE_VOICES.get(language, ()) if voice in available), None)


def tempo_filters(factor):
    filters = []
    while factor > 2:
        filters.append('atempo=2')
        factor /= 2
    filters.append(f'atempo={factor:.8f}')
    return ','.join(filters)


def utterance(event, profile, language, voice, engine, temporary, ffmpeg, rate, mode):
    window = event['end'] - event['start']
    if mode == 'tone-check':
        # A deterministic signal for exercising mixing/encoding, explicitly not multilingual speech.
        count = int(min(2.0, window - .1) * rate)
        number = int(profile['id'].rsplit('_', 1)[1])
        samples = array('f', (.18 * math.sin(2 * math.pi * (220 + 67 * number) * i / rate)
                              * min(1, i / (rate * .03), (count - i) / (rate * .03))
                              for i in range(count)))
        return samples, 1.0
    text = event.get('text', event.get('texts', {}).get(language))
    if not text:
        raise RuntimeError(f'No authored text for {event["id"]} in {language}')
    raw, converted = temporary / 'utterance.wav', temporary / 'converted.wav'
    run([engine, '-v', voice + '+' + profile['variant'], '-p', str(profile['pitch']),
         '-s', str(profile['speed_wpm']), '--stdin', '-w', str(raw)], input=text.encode('utf-8'))
    run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(raw),
         '-ac', '1', '-ar', str(rate), '-c:a', 'pcm_s16le', str(converted)])
    samples, _ = read_wav(converted)
    if not samples or metrics(samples)['rms_dbfs'] is None:
        raise RuntimeError(f'TTS produced no audio for {event["id"]}')
    factor = max(1, len(samples) / rate / (window - .1))
    if factor > 1:
        run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(converted),
             '-af', tempo_filters(factor), '-ac', '1', '-ar', str(rate),
             '-c:a', 'pcm_s16le', str(raw)])
        samples, _ = read_wav(raw)
    # Never truncate speech to fit: a bad schedule must fail instead of mislabelling words.
    if len(samples) / rate > window:
        raise RuntimeError(f'TTS utterance {event["id"]} is longer than its time window')
    return normalized(samples, -20, -6), factor


def noise(kind, count, rate, seed, speech_clips, mode):
    rng = random.Random(seed)
    if kind == 'none':
        return array('f', [0]) * count
    if kind == 'white':
        return array('f', (rng.uniform(-1, 1) for _ in range(count)))
    if kind == 'pink':
        # Fixed IIR pink approximation; seed and recurrence are recorded for repeatability.
        b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
        output = array('f')
        for _ in range(count):
            white = rng.uniform(-1, 1)
            b0 = .99886 * b0 + white * .0555179
            b1 = .99332 * b1 + white * .0750759
            b2 = .96900 * b2 + white * .1538520
            b3 = .86650 * b3 + white * .3104856
            b4 = .55000 * b4 + white * .5329522
            b5 = -.7616 * b5 - white * .0168980
            output.append(b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * .5362)
            b6 = white * .115926
        return output
    if kind == 'room_chatter':
        output = array('f', [0]) * count
        # Overlap three existing TTS clips as non-target background voices. No real room claim.
        for track, clip in enumerate(speech_clips[:3]):
            offset = rng.randrange(len(clip))
            for i in range(count):
                envelope = .5 + .5 * math.sin(2 * math.pi * i / rate / (3.7 + track)) ** 2
                output[i] += clip[(i + offset) % len(clip)] * envelope
        return output
    if kind == 'impulse':
        output = array('f', [0]) * count
        location = int(.3 * rate)
        width = int(.2 * rate)
        while location < count:
            amplitude = rng.uniform(.5, 1) * rng.choice((-1, 1))
            for j in range(min(width, count - location)):
                output[location + j] += amplitude * math.exp(-j / (rate * .04))
            location += int(rng.uniform(.65, 1.25) * rate)
        return output
    raise RuntimeError(f'Unknown noise kind: {kind}')


def generated_reference(case, events, profiles, duration, mode):
    used = list(dict.fromkeys(event['speaker_id'] for event in events))
    segments = [{key: event[key] for key in ('id', 'speaker_id', 'start', 'end', 'text')} for event in events]
    roster = []
    for identifier in used:
        turns = [event for event in events if event['speaker_id'] == identifier]
        roster.append({'speaker_id': identifier, 'first_seen_seconds': turns[0]['start'],
                       'first_segment_id': turns[0]['id'], 'segment_ids': [s['id'] for s in turns],
                       'return_segment_ids': [s['id'] for s in turns[1:]]})
    return {'title': 'مرجع صوت اصطناعي: ' + case['id'], 'language': 'ar',
            'duration_seconds': duration, 'segments': segments,
            'speakers': [{'id': identifier, 'name': profiles[identifier]['name'], 'matched_reference': False}
                         for identifier in used],
            'minutes': {'summary': 'مرجع توليد اصطناعي؛ لا يمثل تحليل مزود أو قياس دقة.',
                        'discussion_points': [], 'decisions': [], 'action_items': [], 'open_questions': [],
                        'speech_annotations': [{'segment_id': event['id'], 'language': event['language'],
                                                'status': 'clear' if mode == 'speech' else 'uninterpretable',
                                                'reason': 'لغة النص المستخدم في التوليد، وليست كشفًا آليًا.'
                                                          if mode == 'speech' else 'نغمة اختبار بلا كلام.'}
                                               for event in events]},
            '_demo': True, '_fixture': {'synthetic': True, 'content_kind': mode,
                                      'notice_ar': 'مرجع أصوات اصطناعية؛ لا يقيس مسافة أو هوية أو دقة تعرف.',
                                      'speech_content_present': mode == 'speech',
                                      'expected_speaker_count': len(used), 'expected_roster': roster}}


def generate(args):
    plan = json.loads((ROOT / 'fixtures/audio-plan.json').read_text(encoding='utf-8'))
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is required; no audio was generated')
    engine = shutil.which('espeak-ng') or shutil.which('espeak')
    if args.mode == 'speech' and not engine:
        raise RuntimeError('Install espeak-ng for synthetic speech. --mode tone-check only validates non-speech signals.')
    available = voices(engine) if args.mode == 'speech' else set()
    cases = [case for case in plan['cases'] if args.cases == 'all' or case['id'] == args.cases]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / 'references').mkdir(exist_ok=True)
    (output / 'meetings').mkdir(exist_ok=True)
    for source in (ROOT / 'fixtures/meetings').glob('*.json'):
        shutil.copyfile(source, output / 'meetings' / source.name)
    catalog = json.loads((ROOT / 'fixtures/index.json').read_text(encoding='utf-8'))
    for item in catalog['fixtures']:
        item['asset'] = 'meetings/' + item['id'] + '.json'
    dump(output / 'reference-catalog.json', catalog)
    shutil.copyfile(ROOT / 'fixtures/audio-plan.json', output / 'audio-plan.json')
    (output / 'README_AR.txt').write_text(
        'ملفات اصطناعية فقط؛ لم تُشغّل خدمة التعرف الآلي على هذه المجموعة.\n'
        'معرفات المتحدثين هي حقيقة التوليد وليست نتيجة تعرف أو أسماء أشخاص حقيقيين.\n'
        'راجع manifest.json للغات المولدة واللغات المتروكة والضجيج والمستويات والأحجام.\n'
        'ملفات *-reference.json تعطي النص والتوقيت الفعليين للصوت المولد؛ مجلد meetings يحوي أمثلة نصية منفصلة.\n'
        'القرب والبعد تخفيف رقمي فقط؛ لا توجد مسافة مقاسة أو نتيجة أداء ميكروفون.\n'
        + ('هذا التشغيل يحوي كلامًا اصطناعيًا من TTS.\n' if args.mode == 'speech' else 'هذا التشغيل tone-check يحوي نغمات فقط ولا يحوي كلامًا أو تجربة لغة.\n'),
        encoding='utf-8')
    rate, seed = plan['sample_rate'], args.seed
    manifest = {'version': 1, 'mode': args.mode, 'seed': seed, 'sample_rate': rate,
                'notice_ar': plan['notice_ar'], 'speech_content_present': args.mode == 'speech',
                'recognition_run': False, 'real_distance_measured': False, 'identity_source': 'generator_truth_labels',
                'tts_engine': Path(engine).name if args.mode == 'speech' else None,
                'tts_version': run([engine, '--version']).decode(errors='replace').strip() if args.mode == 'speech' else None,
                'available_voice_languages': sorted(available), 'cases': [],
                'limits': {'duration_seconds_per_file': 180, 'bytes_per_file': MAX_BYTES,
                           'named_reference_upload_limit': 4, 'reference_min_seconds': 2, 'reference_max_seconds': 10},
                'room_chatter_kind': 'overlapping_synthetic_TTS' if args.mode == 'speech' else 'overlapping_non_speech_tones'}
    with tempfile.TemporaryDirectory(prefix='majlis-fixtures-') as temporary_name:
        temporary = Path(temporary_name)
        for case_number, case in enumerate(cases):
            duration = case['duration_seconds']
            if not 0 < duration < 180:
                raise RuntimeError('Fixture durations must remain below three minutes')
            profiles = {profile['id']: profile for profile in case['speakers']}
            selected_language = None
            if case['id'] == 'recurring_six':
                selected_language = args.language if args.language != 'auto' else ('ar' if voice_for('ar', available) else 'en')
                if args.mode == 'speech' and not voice_for(selected_language, available):
                    raise RuntimeError(f'TTS does not list requested language {selected_language}; no substitution')
            foreground = array('f', [0]) * int(duration * rate)
            events, skipped, reference_files, first_clips = [], [], [], {}
            for event in case['events']:
                language = selected_language or event['language']
                voice = voice_for(language, available)
                if args.mode == 'speech' and not voice:
                    skipped.append({'event_id': event['id'], 'speaker_id': event['speaker_id'], 'language': language,
                                    'reason': 'unsupported_by_local_TTS_voice_list', 'substitute_used': False})
                    continue
                samples, factor = utterance(event, profiles[event['speaker_id']], language, voice,
                                             engine, temporary, ffmpeg, rate, args.mode)
                gain = 10 ** (event['speech_gain_db'] / 20)
                start = int(event['start'] * rate)
                for i, sample in enumerate(samples):
                    foreground[start + i] += sample * gain
                text = event.get('text', event.get('texts', {}).get(language))
                actual = {'id': event['id'], 'speaker_id': event['speaker_id'], 'start': event['start'],
                          'end': round(event['start'] + len(samples) / rate, 6),
                          'text': text if args.mode == 'speech' else '[نغمة اختبار اصطناعية؛ ليست كلامًا]',
                          'language': language if args.mode == 'speech' else 'unknown',
                          'requested_language': language, 'speech_gain_db': event['speech_gain_db'],
                          'voice': voice + '+' + profiles[event['speaker_id']]['variant'] if voice else None,
                          'tempo_factor': round(factor, 8), 'clean_clip_metrics': metrics(samples)}
                events.append(actual)
                if event['speaker_id'] not in first_clips:
                    first_clips[event['speaker_id']] = samples
                    base = output / 'references' / (case['id'] + '-' + event['speaker_id'])
                    reference_samples = reference_clip(samples, rate)
                    paths = export_audio(base, reference_samples, rate, args.format, temporary, ffmpeg)
                    reference_files.append({'speaker_id': event['speaker_id'], 'source_event_id': event['id'],
                                            'duration_seconds': len(reference_samples) / rate,
                                            'speech_duration_seconds': len(samples) / rate,
                                            'silence_padding_seconds': (len(reference_samples) - len(samples)) / rate,
                                            'files': [str(path.relative_to(output)) for path in paths],
                                            'identity_is_generator_truth': True})
            if not events:
                manifest['cases'].append({'id': case['id'], 'selected_language': selected_language,
                                          'status': 'skipped_no_supported_TTS_languages',
                                          'duration_seconds': duration, 'timeline': [], 'skipped_events': skipped,
                                          'expected_speaker_count': 0, 'reference_meeting': None,
                                          'speaker_references': [], 'variants': []})
                print(f'{case["id"]}: skipped; no locally supported TTS language, no substitute audio')
                continue
            reference = generated_reference(case, events, profiles, duration, args.mode)
            reference_name = case['id'] + '-reference.json'
            dump(output / reference_name, reference)
            case_manifest = {'id': case['id'], 'selected_language': selected_language,
                             'duration_seconds': duration, 'timeline': events, 'skipped_events': skipped,
                             'requested_languages': list(dict.fromkeys(event.get('language', selected_language) for event in case['events'])),
                             'generated_languages': list(dict.fromkeys(event['language'] for event in events)),
                             'expected_speaker_count': len(first_clips), 'reference_meeting': reference_name,
                             'speaker_references': reference_files,
                             'suggested_named_reference_speaker_ids': list(first_clips)[:4], 'variants': []}
            for variant_number, variant in enumerate(case['noise_variants']):
                variant_seed = seed + case_number * 100 + variant_number
                background = noise(variant['kind'], len(foreground), rate, variant_seed, list(first_clips.values()), args.mode)
                if variant['rms_dbfs'] is not None:
                    background = normalized(background, variant['rms_dbfs'])
                mixture = array('f', (a + b for a, b in zip(foreground, background)))
                peak = max(abs(x) for x in mixture)
                mix_gain = min(1.0, .95 / max(peak, 1e-10))
                if mix_gain < 1:
                    mixture = array('f', (x * mix_gain for x in mixture))
                base = output / (case['id'] + '-' + variant['id'])
                paths = export_audio(base, mixture, rate, args.format, temporary, ffmpeg)
                stats = metrics(mixture)
                per_event = []
                for event in events:
                    begin, end = round(event['start'] * rate), round(event['end'] * rate)
                    speech_power = sum(float(x) ** 2 for x in foreground[begin:end])
                    noise_power = sum(float(x) ** 2 for x in background[begin:end])
                    per_event.append({'event_id': event['id'],
                                      'effective_speech_gain_db': round(event['speech_gain_db'] + 20 * math.log10(mix_gain), 6),
                                      'digital_snr_db': None if noise_power == 0 else round(10 * math.log10(speech_power / noise_power), 6)})
                case_manifest['variants'].append({'id': variant['id'], 'noise_kind': variant['kind'],
                    'noise_seed': variant_seed, 'target_noise_rms_dbfs': variant['rms_dbfs'],
                    'noise_metrics_before_mix_gain': metrics(background), 'mix_gain_db': round(20 * math.log10(mix_gain), 6),
                    'pcm_metrics_before_encoding': stats, 'per_event_conditions': per_event,
                    'files': [{'path': str(path.relative_to(output)), 'bytes': path.stat().st_size,
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]})
            manifest['cases'].append(case_manifest)
            print(f'{case["id"]}: {len(events)} events, {len(first_clips)} synthetic foreground voices, '
                  f'{len(skipped)} explicitly skipped events; mode={args.mode}')
    dump(output / 'manifest.json', manifest)
    print(f'Wrote {len(manifest["cases"])} cases to {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'fixtures/generated'))
    parser.add_argument('--mode', choices=('speech', 'tone-check'), default='speech')
    parser.add_argument('--format', choices=('m4a', 'wav', 'both'), default='m4a')
    parser.add_argument('--language', choices=('auto', 'ar', 'en'), default='auto')
    parser.add_argument('--cases', choices=('all', 'recurring_six', 'multilingual_eight'), default='all')
    parser.add_argument('--seed', type=int, default=20261010)
    args = parser.parse_args()
    try:
        generate(args)
    except (RuntimeError, OSError, ValueError) as error:
        parser.exit(1, f'Fixture generation failed: {error}\n')


if __name__ == '__main__':
    main()
