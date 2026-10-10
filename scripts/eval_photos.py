"""Run the label reader over a folder of annotated photos and report each field against the reviewed answers.

    python scripts/eval_photos.py <dataset dir with annotations.jsonl> [output.json]

Only fields whose annotation state is "visible" count as exact answers; "not_visible", "uncertain" and "placeholder"
fields are checked for the reader staying silent. Nothing is looked up by file name or hash.
"""
import base64, io, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from PIL import Image  # noqa: E402
from batterycheck import ocr, vision  # noqa: E402

FIELDS = ('brand', 'model', 'serial_number')
norm = lambda value: ''.join(str(value or '').upper().split())


def upload(path):
    # Same limits as the browser: longest side 2400px.
    image = Image.open(path).convert('RGB')
    image.thumbnail((2400, 2400))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=92)
    return base64.b64encode(buffer.getvalue()).decode()


def main(root, out=None):
    root, rows = Path(root), []
    for line in (root / 'annotations.jsonl').read_text().splitlines():
        note = json.loads(line)
        started = time.time()
        try:
            read = vision.combine(ocr.extract_label(upload(root / note['image'])), None)
        except Exception as exc:  # a reader error is a result too
            read = {'status': 'error', 'error': type(exc).__name__}
        row = {'image': note['image'], 'group': note['group_id'], 'review': note['annotation_status'], 'status': read.get('status'),
               'seconds': round(time.time() - started, 1), 'fields': {}}
        for field in FIELDS:
            truth = (note.get('image_ground_truth') or {}).get(field) or {'value': None, 'state': 'unreviewed'}
            got = read.get(field)
            if truth['state'] == 'inferred_from_domain':  # the brand is a guess from a web address and must be labelled as one
                verdict = 'inferred_labelled' if read.get('brand_source') == 'website_domain' else 'silent_ok' if not got else 'returned_unverified'
            elif truth['state'] == 'visible':
                verdict = 'exact' if norm(got) == norm(truth['value']) else 'missed' if not got else 'wrong'
            elif truth['state'] == 'unreviewed':
                verdict = 'unreviewed'
            else:  # nothing certain to read: silence is right, a value needs a human look
                verdict = 'silent_ok' if not got else 'returned_unverified'
            row['fields'][field] = {'expected': truth['value'], 'state': truth['state'], 'got': got, 'verdict': verdict}
        rows.append(row)
    summary = {field: {} for field in FIELDS}
    for row in rows:
        for field in FIELDS:
            verdict = row['fields'][field]['verdict']
            summary[field][verdict] = summary[field].get(verdict, 0) + 1
    report = {'engine': ocr.engine_status()['engine'], 'images': len(rows), 'summary': summary, 'rows': rows}
    if out:
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for row in rows:
        if row['review'] != 'needs_review':
            print(row['image'].split('/', 1)[1][:34].ljust(34), row['status'], ' | '.join(f"{f}: {v['got']} ({v['verdict']})" for f, v in row['fields'].items()))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main(*sys.argv[1:3])
