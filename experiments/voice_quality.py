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

# Fixed additional public generated samples, declared before observing output.
# These are inert strings, never computer commands.
def expanded_corpus():
    return (
        ("tr-2","tr","Lütfen bu metni Türkçe olarak oku."),
        ("tr-3","tr","Yarın sabah hava durumunu kontrol etmek istiyorum."),
        ("en-2","en","Please read this short message aloud."),
        ("en-3","en","I would like to check the weather tomorrow morning."),
        ("de-2","de","Bitte lies diese kurze Nachricht vor."),
        ("de-3","de","Ich möchte morgen früh das Wetter prüfen."),
    )

def speech_filename(identity):
    """Closed fixture sample identity, not a caller-supplied filesystem path."""
    if type(identity) is not str or not re.fullmatch(r"(?:tr|en|de)(?:-[23])?",identity):
        raise Refused("invalid_speech_sample_identity")
    return identity+".wav"

def targeted_corpus():
    """Preserve the two failed Small utterances and one passing EN control."""
    by_id={sample[0]:sample for sample in expanded_corpus()}
    return (by_id["tr-3"],by_id["de-2"],("en","en","Hello. Today is a beautiful day."))

def turbo_short_args(rate,frames):
    """Fixed short-audio fixture profile only: 10s context, greedy decode.
    Reject longer inputs rather than truncating to a smaller context.
    """
    if (type(rate) is not int or type(frames) is not int
            or rate!=22050 or not 0<frames<=rate*8):
        raise Refused("turbo_short_audio_limit")
    return ["-ac","500","-bs","1","-bo","1"]
