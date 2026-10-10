#!/usr/bin/env python3
"""Offline fixture contracts and real WAV/AAC encoding checks; no cloud recognition."""
from array import array
import base64
import codecs
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEECH_LANGUAGES = {'ar', 'en', 'fr', 'de', 'es', 'tr', 'he', 'ru', 'el', 'uk', 'zh', 'fa', 'ur', 'mul', 'other', 'unknown'}
spec = importlib.util.spec_from_file_location('fixture_generator', ROOT / 'scripts/generate-fixtures.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


class FixtureContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load(ROOT / 'fixtures/index.json')
        cls.meetings = {entry['id']: load(ROOT / 'fixtures/meetings' / (entry['id'] + '.json'))
                        for entry in cls.catalog['fixtures']}

    def test_catalog_android_assets_are_exact_source_copies(self):
        self.assertEqual(len(self.meetings), 4)
        self.assertEqual(self.catalog, load(ROOT / 'android/app/src/main/assets/fixtures/index.json'))
        for entry in self.catalog['fixtures']:
            with self.subTest(entry=entry['id']):
                meeting = self.meetings[entry['id']]
                self.assertEqual(meeting, load(ROOT / 'android/app/src/main/assets' / entry['asset']))
                self.assertEqual(entry['speaker_count'], len(meeting['speakers']))
                self.assertEqual(entry['segment_count'], len(meeting['segments']))

    def test_analysis_fields_timestamps_and_grounded_citations(self):
        for identifier, meeting in self.meetings.items():
            with self.subTest(fixture=identifier):
                self.assertEqual(meeting['language'], 'ar')
                self.assertLess(meeting['duration_seconds'], 180)
                speakers = {speaker['id'] for speaker in meeting['speakers']}
                ids = {segment['id'] for segment in meeting['segments']}
                self.assertEqual(len(speakers), len(meeting['speakers']))
                self.assertEqual(len(ids), len(meeting['segments']))
                previous_end = 0
                for segment in meeting['segments']:
                    self.assertLessEqual(previous_end, segment['start'])
                    self.assertLess(segment['start'], segment['end'])
                    self.assertLessEqual(segment['end'], meeting['duration_seconds'])
                    self.assertIn(segment['speaker_id'], speakers)
                    self.assertTrue(segment['text'].strip())
                    previous_end = segment['end']
                annotations = meeting['minutes']['speech_annotations']
                self.assertEqual(len(annotations), len(ids))
                self.assertEqual({item['segment_id'] for item in annotations}, ids)
                for item in annotations:
                    self.assertIn(item['language'], SPEECH_LANGUAGES)
                    self.assertIn(item['status'], ('clear', 'unclear', 'uninterpretable'))
                    self.assertTrue(item['reason'].strip())
                for item in meeting['minutes']['decisions'] + meeting['minutes']['action_items']:
                    self.assertTrue(item['segment_ids'])
                    self.assertTrue(set(item['segment_ids']).issubset(ids))

    def test_simulation_is_explicit_and_no_reference_match_claimed(self):
        for meeting in self.meetings.values():
            fixture = meeting['_fixture']
            self.assertTrue(meeting['_demo'])
            self.assertTrue(fixture['synthetic'])
            self.assertEqual(fixture['content_kind'], 'manually_authored_reference_not_provider_output')
            self.assertEqual(fixture['identity_claim'], 'no_real_person_identification')
            self.assertEqual(fixture['distance_claim'], 'digital_attenuation_only_no_measured_metres')
            self.assertIn('اصطناعية', fixture['notice_ar'])
            self.assertTrue(all(speaker['matched_reference'] is False for speaker in meeting['speakers']))
            events = {item['segment_id']: item for item in fixture['events']}
            self.assertEqual(set(events), {item['id'] for item in meeting['segments']})
            for segment in meeting['segments']:
                event = events[segment['id']]
                self.assertEqual(event['speaker_id'], segment['speaker_id'])
                self.assertEqual((event['start'], event['end']), (segment['start'], segment['end']))

    def test_late_appearances_and_returns_preserve_truth_ids(self):
        meeting = self.meetings['six-speakers-distance']
        seen = set()
        counts = []
        for segment in meeting['segments']:
            seen.add(segment['speaker_id'])
            counts.append(len(seen))
        self.assertEqual(counts, [1, 2, 3, 3, 4, 4, 5, 5, 5, 6, 6, 6, 6])
        self.assertEqual(meeting['segments'][9]['start'], 99)
        for meeting in self.meetings.values():
            self.assertEqual(meeting['_fixture']['expected_speaker_count'], len(meeting['speakers']))
            for expected in meeting['_fixture']['expected_roster']:
                turns = [s for s in meeting['segments'] if s['speaker_id'] == expected['speaker_id']]
                self.assertEqual(expected['first_seen_seconds'], turns[0]['start'])
                self.assertEqual(expected['first_segment_id'], turns[0]['id'])
                self.assertEqual(expected['segment_ids'], [s['id'] for s in turns])
                self.assertEqual(expected['return_segment_ids'], [s['id'] for s in turns[1:]])

    def test_eight_authored_languages_and_known_ciphers(self):
        languages = {s['language'] for s in self.meetings['multilingual-eight']['minutes']['speech_annotations']}
        self.assertEqual(languages, {'he', 'ru', 'el', 'uk', 'zh', 'fa', 'ur', 'ar'})
        meeting = self.meetings['unknown-and-ciphers']
        segments = {s['id']: s['text'] for s in meeting['segments']}
        morse = {'...': 'S', '---': 'O', '.----': '1', '..---': '2', '...--': '3'}
        for example in meeting['_fixture']['encoding_examples']:
            source, method = example['input'], example['method']
            self.assertEqual(source, segments[example['segment_id']])
            if method == 'base64':
                result = base64.b64decode(source, validate=True).decode('utf-8')
            elif method == 'hex':
                result = bytes.fromhex(source).decode('utf-8')
            elif method == 'rot13':
                result = codecs.decode(source, 'rot_13')
            elif method == 'caesar':
                shift = example['options']['shift']
                result = ''.join(chr((ord(c) - ord('A') - shift) % 26 + ord('A')) if 'A' <= c <= 'Z' else c for c in source)
            elif method == 'morse':
                result = ' '.join(''.join(morse[c] for c in word.split()) for word in source.split(' / '))
            else:
                self.assertEqual(method, 'unknown')
                self.assertIsNone(example['expected_plaintext'])
                continue
            self.assertEqual(result, example['expected_plaintext'])
        self.assertEqual(meeting['minutes']['decisions'], [])
        self.assertEqual(meeting['minutes']['action_items'], [])

    def test_backend_strict_schema_when_dependencies_available(self):
        try:
            sys.path.insert(0, str(ROOT / 'backend'))
            from app.models import TranslationRequest
        except ImportError:
            self.skipTest('Backend dependencies unavailable; stdlib contract checks still ran')
        for meeting in self.meetings.values():
            public = {key: meeting[key] for key in ('title', 'language', 'duration_seconds', 'speakers', 'segments', 'minutes')}
            parsed = TranslationRequest.model_validate({'meeting': public, 'target_language': 'en'})
            self.assertEqual(len(parsed.meeting.segments), len(meeting['segments']))

    def test_audio_plan_bounds_and_supported_language_policy(self):
        plan = load(ROOT / 'fixtures/audio-plan.json')
        self.assertEqual(plan['max_file_bytes'], 24_000_000)
        for case in plan['cases']:
            self.assertLess(case['duration_seconds'], 180)
            known = {profile['id'] for profile in case['speakers']}
            previous_end = 0
            for event in case['events']:
                self.assertIn(event['speaker_id'], known)
                self.assertGreaterEqual(event['start'], previous_end)
                self.assertLess(event['start'], event['end'])
                self.assertLessEqual(event['end'], case['duration_seconds'])
                self.assertLessEqual(event['speech_gain_db'], 0)
                previous_end = event['end']
        self.assertEqual(generator.voice_for('he', {'en', 'ru'}), None)
        self.assertEqual(generator.voice_for('zh', {'cmn', 'en'}), 'cmn')


class RealAudioBoundaries(unittest.TestCase):
    def test_reference_clip_bounds_preserve_speech_and_pad_only_silence(self):
        rate = 16000
        speech = array('f', [.1, -.1]) * (rate // 2)
        reference = generator.reference_clip(speech, rate)
        self.assertEqual(len(reference), 2 * rate)
        self.assertEqual(reference[:len(speech)].tobytes(), speech.tobytes())
        self.assertTrue(all(sample == 0 for sample in reference[len(speech):]))
        full = array('f', [.1]) * (10 * rate)
        self.assertEqual(generator.reference_clip(full, rate).tobytes(), full.tobytes())
        with self.assertRaises(RuntimeError):
            generator.reference_clip(array('f', [.1]) * (10 * rate + 1), rate)

    def test_seeded_noise_and_declared_digital_gain(self):
        rate, count = 16000, 16000
        clip = array('f', (.2 * math.sin(2 * math.pi * 440 * i / rate) for i in range(count)))
        for kind in ('white', 'pink', 'room_chatter', 'impulse'):
            with self.subTest(kind=kind):
                a = generator.noise(kind, count, rate, 42, [clip, clip, clip], 'tone-check')
                b = generator.noise(kind, count, rate, 42, [clip, clip, clip], 'tone-check')
                c = generator.noise(kind, count, rate, 43, [clip, clip, clip], 'tone-check')
                self.assertEqual(a.tobytes(), b.tobytes())
                self.assertNotEqual(a.tobytes(), c.tobytes())
                normalized = generator.normalized(a, -32)
                self.assertAlmostEqual(generator.metrics(normalized)['rms_dbfs'], -32, places=4)
                self.assertEqual(generator.metrics(normalized)['clipped_samples'], 0)
        original = generator.metrics(clip)['rms_dbfs']
        attenuated = generator.metrics(array('f', (sample * 10 ** (-18 / 20) for sample in clip)))['rms_dbfs']
        self.assertAlmostEqual(original - attenuated, 18, places=4)

    def test_noise_color_and_impulse_structure(self):
        rate, count = 16000, 16000
        white = generator.noise('white', count, rate, 77, [], 'tone-check')
        pink = generator.noise('pink', count, rate, 77, [], 'tone-check')
        impulse = generator.noise('impulse', count, rate, 77, [], 'tone-check')
        def adjacent_correlation(samples):
            return sum(samples[i] * samples[i+1] for i in range(count-1)) / sum(x*x for x in samples)
        self.assertLess(abs(adjacent_correlation(white)), .05)
        self.assertGreater(adjacent_correlation(pink), .6)
        self.assertGreater(sum(x == 0 for x in impulse) / count, .5)

    def test_real_wav_aac_duration_rms_clipping_and_determinism(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('ffmpeg unavailable; real codec test cannot run')
        rate = 16000
        samples = array('f', (.1 * math.sin(2 * math.pi * 440 * i / rate) for i in range(rate * 2)))
        with tempfile.TemporaryDirectory(prefix='majlis-fixture-test-') as folder:
            temporary = Path(folder)
            paths = generator.export_audio(temporary / 'one', samples, rate, 'both', temporary, ffmpeg)
            generator.export_audio(temporary / 'two', samples, rate, 'both', temporary, ffmpeg)
            for suffix in ('.wav', '.m4a'):
                self.assertEqual(hashlib.sha256((temporary / ('one' + suffix)).read_bytes()).digest(),
                                 hashlib.sha256((temporary / ('two' + suffix)).read_bytes()).digest())
            restored, actual_rate = generator.read_wav(paths[0])
            self.assertEqual(actual_rate, rate)
            self.assertEqual(len(restored), len(samples))
            self.assertAlmostEqual(generator.metrics(restored)['rms_dbfs'], generator.metrics(samples)['rms_dbfs'], places=2)
            generator.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(paths[1]),
                           '-ac', '1', '-ar', str(rate), '-c:a', 'pcm_s16le', str(temporary / 'decoded.wav')])
            decoded, _ = generator.read_wav(temporary / 'decoded.wav')
            self.assertAlmostEqual(len(decoded) / rate, 2, delta=.08)
            self.assertEqual(generator.metrics(decoded)['clipped_samples'], 0)
            self.assertAlmostEqual(generator.metrics(decoded)['rms_dbfs'], generator.metrics(samples)['rms_dbfs'], delta=.3)
            self.assertTrue(all(path.stat().st_size < 24_000_000 for path in paths))
            with self.assertRaises(RuntimeError):
                generator.write_wav(temporary / 'clipped.wav', array('f', [1.1]), rate)

    def test_speech_mode_never_silently_uses_tones_when_engine_absent(self):
        if shutil.which('espeak-ng') or shutil.which('espeak'):
            self.skipTest('TTS engine available; missing-engine branch does not apply')
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'output'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/generate-fixtures.py'), '--output', str(destination)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Install espeak-ng', result.stderr)
            self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
