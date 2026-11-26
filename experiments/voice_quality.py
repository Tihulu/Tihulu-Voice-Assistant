# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed synthetic speech quality gate, not a population accuracy benchmark."""
import re
import unicodedata
from gateway import Refused

def words(text,language):
    if type(text) is not str or len(text)>16384 or language not in ("tr","en","de"):
        raise Refused("invalid_quality_input")
    text=unicodedata.normalize("NFC",text)
    if language=="tr":
        text=text.translate(str.maketrans({"I":"ı","İ":"i"}))
    return re.findall(r"[^\W_]+",text.casefold(),flags=re.UNICODE)

def word_errors(reference,observed,language):
    a,b=words(reference,language),words(observed,language)
    if not 1<=len(a)<=64 or len(b)>128:
        raise Refused("quality_word_limit")
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        next_row=[i]
        for j,y in enumerate(b,1):
            next_row.append(min(row[j]+1,next_row[-1]+1,row[j-1]+(x!=y)))
        row=next_row
    return row[-1],len(a)

def acceptable(errors,count):
    # Predeclared 40% WER ceiling and at least three reference words.
    return (type(errors) is int and type(count) is int and 3<=count<=64
            and 0<=errors and errors*5<=count*2)
