"""Retain explicit recommendations and fill missing decisions from the ELU."""

def fill_missing_decisions(frame,elu_field='ELU_CLS'):
    values=frame['Decision'].astype('string').str.strip()
    missing=values.isna() | values.str.upper().isin(['','NULL','NONE','NAN','<NA>'])
    source=frame[elu_field]
    valid=source.notna() & source.astype('string').str.strip().ne('')
    selected=missing & valid
    frame['Decision']=frame['Decision'].astype(object)
    frame.loc[selected,'Decision']=source.loc[selected]
    return int(selected.sum())
