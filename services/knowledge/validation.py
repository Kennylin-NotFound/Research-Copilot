"""Validate model-supplied citation IDs before rendering source references."""
import re

def validate_answer(result,hit_count):
    if not isinstance(result,dict) or result.get('insufficient') is not False:
        return None
    candidate=result.get('answer')
    ids=result.get('cited_ids')
    if not isinstance(candidate,str) or not candidate.strip() or not isinstance(ids,list) or not ids:
        return None
    if not all(type(i) is int and 1<=i<=hit_count for i in ids): return None
    markers={int(i) for i in re.findall(r'\[(\d+)\]',candidate)}
    if not markers<=set(ids): return None
    ids=list(dict.fromkeys(ids))
    if not markers: candidate+=' '+''.join(f'[{i}]' for i in ids)
    return candidate,ids
