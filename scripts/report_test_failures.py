"""Keep concrete CI test failures visible in the job summary as well as logs."""
from html import escape
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def main():
    path = Path('build/windows/tests.xml')
    if not path.is_file():
        return
    sections = []
    labels = []
    for case in ET.parse(path).iter('testcase'):
        for failure in case:
            if failure.tag not in ('failure', 'error'):
                continue
            name = case.get('classname', '') + '::' + case.get('name', '')
            details = failure.text or failure.get('message', '')
            sections.append(f'<details open><summary>{escape(name)}</summary>\n\n<pre>{escape(details[:20000])}</pre></details>')
            message = re.sub(r'\x1b\[[0-9;]*m', '', failure.get('message', ''))
            labels.append({'test': name[-100:], 'reason': ' '.join(message.split())[:230]})
    output = os.environ.get('GITHUB_OUTPUT')
    if output and labels:
        with Path(output).open('a', encoding='utf-8') as destination:
            destination.write('failures=' + json.dumps(labels[:8], ensure_ascii=False) + '\n')
    if not sections:
        return
    report = '### Application test failures\n\n' + '\n\n'.join(sections) + '\n'
    print(report)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as output:
            output.write(report)


if __name__ == '__main__':
    main()
