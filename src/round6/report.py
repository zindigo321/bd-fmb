"""Render observed phenomena from explicit structured booleans only."""
LABELS = {
    'same_point_multiple_satellites': '同点多星',
    'cross_satellite_same_point_revisit': '跨星同点重访',
    'natural_cross_satellite_union': '单段跨星合并',
    'natural_overlap_or_touch': '自然重叠或相接',
}


def phenomena_text(phenomena):
    lines=[]
    for key,label in LABELS.items():
        value=phenomena.get(key,{}).get('observed')
        state=('已观测到；true' if value is True else '未观测到；false' if value is False else '尚未完成核验；null')
        lines.append(f'- {label}（{key}）：{state}。')
    return '\n'.join(lines)+'\n\n'
