"""Preserve fields whose nested types the supplied schema exporter omitted."""
def varint(value):
    output=bytearray()
    while value>127:
        output.append((value&127)|128);value>>=7
    output.append(value)
    return bytes(output)

def read_varint(data,offset):
    value=0
    for shift in range(0,70,7):
        if offset>=len(data):raise ValueError('Truncated varint')
        byte=data[offset];offset+=1;value|=(byte&127)<<shift
        if byte<128:return value,offset
    raise ValueError('Varint overflow')

def fields(data):
    offset=0
    while offset<len(data):
        start=offset
        tag,offset=read_varint(data,offset)
        number,kind=tag>>3,tag&7
        if number==0:raise ValueError('Field zero')
        if kind==0:
            payload,offset=read_varint(data,offset)
        elif kind in (1,5):
            end=offset+(8 if kind==1 else 4)
            payload=data[offset:end];offset=end
        elif kind==2:
            length,offset=read_varint(data,offset)
            end=offset+length;payload=data[offset:end];offset=end
        else:raise ValueError('Unsupported wire type')
        if offset>len(data):raise ValueError('Truncated field')
        yield number,kind,data[start:offset],payload

def byte_field(number,payload):
    return varint(number<<3|2)+varint(len(payload))+payload

def merge(base,override):
    """Replace complete top-level fields, preserving opaque nested data verbatim."""
    replacements=list(fields(override))
    numbers={n for n,_,_,_ in replacements}
    return b''.join(raw for n,_,raw,_ in fields(base) if n not in numbers)+override
