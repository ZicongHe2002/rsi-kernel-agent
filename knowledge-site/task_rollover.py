"""Carry unfinished Markdown list items forward without changing history."""

import re


def task_blocks(content, pending_only=True):
    roots, stack = [], []
    fence = None
    in_log = False
    for line in content.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if fence:
            if stack and indent > stack[-1]['indent']:
                stack[-1]['lines'].append(line)
            if re.fullmatch(re.escape(fence[0]) + '{' + str(fence[1]) + r',}\s*', stripped):
                fence = None
            continue
        opening = re.match(r'(`{3,}|~{3,})', stripped)
        if opening:
            fence = (opening[1][0], len(opening[1]))
            if stack and indent > stack[-1]['indent']:
                stack[-1]['lines'].append(line)
            else:
                stack = []
            continue
        heading = re.match(r'^#{1,2}\s+(.+?)\s*#*\s*$', line)
        if heading:
            in_log = heading[1] == '完成记录'
            stack = []
            continue
        if in_log:
            continue
        item = re.match(r'^( *)([-+*]|\d+[.)])\s+(?:\[([ xX])\]\s+)?(.+)$', line)
        if item:
            if not stack and indent >= 4:
                continue  # Indented code examples are not tasks.
            while stack and stack[-1]['indent'] >= indent:
                stack.pop()
            node = {'indent': indent, 'checked': item[3], 'lines': [line], 'children': []}
            (stack[-1]['children'] if stack else roots).append(node)
            stack.append(node)
        elif stack and (not stripped or indent > stack[-1]['indent']):
            stack[-1]['lines'].append(line)
        elif stripped:
            stack = []

    def render(node, details=False):
        keep = (node['checked'] is not None and (not pending_only or node['checked'] == ' ')) or (node['checked'] is None and details)
        children = [text for child in node['children'] if (text := render(child, keep))]
        if not keep and not children:
            return ''
        lines = list(node['lines'])
        if not keep:
            # A completed parent remains plain context for an unfinished child.
            lines[0] = re.sub(r'\[[xX]\]\s+', '', lines[0], count=1)
        return '\n'.join(lines).rstrip() + (('\n' + '\n'.join(children)) if children else '')

    return [text for root in roots if (text := render(root))]


def task_key(block):
    return re.sub(r'(?m)^\s*(?:[-+*]|\d+[.)])\s+\[[ xX]\]\s+', '', block).strip()


def carry_content(current, previous, week):
    marker = f'<!-- zhixu-weekly-carry:{week} -->'
    if marker in current:
        return current
    seen = {task_key(block) for block in task_blocks(current, pending_only=False)}
    carried = []
    for block in task_blocks(previous):
        key = task_key(block)
        if key not in seen:
            carried.append(block)
            seen.add(key)
    newline = '\r\n' if '\r\n' in current else '\r' if '\r' in current else '\n'
    addition = ('\n\n'.join(carried) + '\n\n' if carried else '') + marker + '\n'
    addition = addition.replace('\n', newline)
    # Keep records at the end, and preserve every byte of existing note text.
    match = re.search(r'(?m)^## 完成记录[^\r\n]*(?:\r?\n|\r|$)', current)
    position = match.start() if match else len(current)
    return current[:position] + newline + addition + newline + current[position:]
