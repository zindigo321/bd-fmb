"""UTF-8 reports from source literals, never Chinese source through shell stdin."""
from pathlib import Path
import re
from src.round2.evidence import write, digest

KNOWN_SENTENCE = '固定网格真实正事件、多星汇总与重访统计分别验收。'


def audit_text(text):
    issues=[]
    if '\ufffd' in text:issues.append('UNICODE_REPLACEMENT_CHARACTER')
    if re.search(r'\?{3,}', text):issues.append('SUSPICIOUS_QUESTION_MARK_RUN')
    if KNOWN_SENTENCE not in text:issues.append('KNOWN_CHINESE_SENTENCE_MISSING')
    return dict(status='FAIL' if issues else 'PASS',issues=issues,
                known_sentence=KNOWN_SENTENCE,exact_sentence_present=KNOWN_SENTENCE in text)


def save_report(out, summary, text):
    out=Path(out)
    path=out/'audit_report.md'
    temp=path.with_suffix('.md.tmp');temp.write_bytes(text.encode('utf-8'));temp.replace(path)
    back=path.read_bytes().decode('utf-8',errors='strict')
    audit=audit_text(back);audit.update(byte_roundtrip=back==text,sha256=digest(path.read_bytes()),encoding='UTF-8')
    write(out/'encoding_audit.json',audit)
    # Reporting success is an independent dimension. Never rewrite acceptance.
    report_summary=dict(summary,report_generated=audit['status']=='PASS',report_encoding=audit)
    write(out/'report_summary.json',report_summary)
    if audit['status']!='PASS':raise ValueError('Report content encoding validation failed')
    return report_summary
