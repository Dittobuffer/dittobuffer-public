"""Transfer an acquired same-species palette into an accepted legacy UV atlas.

Each output RGB is an actual RGB from the pinned shiny source. Legacy alpha
and pixel positions stay unchanged. Nearest normal-source colors select the
corresponding shiny colors; no interpolated or hand-painted colors are used.
This is a compatibility conversion, not exact original-texture fidelity.
"""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
def rgb_code(rgb):
    v=np.asarray(rgb,dtype=np.uint64);return (v[...,0]<<16)|(v[...,1]<<8)|v[...,2]
def unpack(code):return np.stack([(code>>16)&255,(code>>8)&255,code&255],axis=-1).astype(np.uint8)
def source_table(normal,shiny,alpha_is_data=False):
    a=np.asarray(normal.convert('RGBA'));b=np.asarray(shiny.convert('RGBA'))
    if a.shape!=b.shape:raise ValueError('Paired source atlases must have the same dimensions')
    valid=np.ones(a.shape[:2],dtype=bool) if alpha_is_data else a[...,3]>0
    if not valid.any():valid=np.ones(a.shape[:2],dtype=bool) # Opaque native shaders may use RGB with zero data alpha.
    keys=rgb_code(a[valid,:3]);targets=rgb_code(b[valid,:3]);pairs,counts=np.unique((keys<<24)|targets,return_counts=True);group=pairs>>24
    order=np.lexsort((-counts,group));ordered=group[order];first=np.r_[True,ordered[1:]!=ordered[:-1]];chosen=order[first];normal_codes=group[chosen];shiny_codes=pairs[chosen]&0xffffff
    unique,totals=np.unique(keys,return_counts=True)
    if not np.array_equal(unique,normal_codes):raise ValueError('Palette group order invalid')
    return unpack(normal_codes),unpack(shiny_codes),totals,dict(source_colors=len(unique),ambiguous_source_colors=int(len(pairs)-len(unique)))
def nearest(colors,table):
    values=np.asarray(table,dtype=np.float32);indices=[];errors=[]
    for offset in range(0,len(colors),128):
        diff=np.asarray(colors[offset:offset+128],dtype=np.float32)[:,None,:]-values[None,:,:];distance=np.sum(diff*diff,axis=-1);chosen=distance.argmin(axis=1);indices.extend(chosen.tolist());errors.extend(np.sqrt(distance[np.arange(len(chosen)),chosen]/3).tolist())
    return np.array(indices,dtype=np.int64),np.array(errors,dtype=float)
def compatibility_score(legacy,normal,shiny,alpha_is_data=False):
    table,_,frequency,_=source_table(normal,shiny,alpha_is_data);a=np.asarray(legacy.convert('RGBA'));valid=np.ones(a.shape[:2],dtype=bool) if alpha_is_data else a[...,3]>0
    if not valid.any():valid=np.ones(a.shape[:2],dtype=bool)
    colors,counts=np.unique(a[valid,:3],axis=0,return_counts=True);indices,errors=nearest(colors,table);probability=frequency[indices]/frequency.sum()
    return float(np.average(errors+.2*(-np.log(np.maximum(probability,1e-9))),weights=counts))
def transfer(legacy,normal,shiny,alpha_is_data=False):
    table,targets,_,report=source_table(normal,shiny,alpha_is_data);a=np.asarray(legacy.convert('RGBA')).copy();valid=np.ones(a.shape[:2],dtype=bool) if alpha_is_data else a[...,3]>0
    if not valid.any():valid=np.ones(a.shape[:2],dtype=bool)
    colors,inverse,counts=np.unique(a[valid,:3],axis=0,return_inverse=True,return_counts=True);indices,errors=nearest(colors,table);a[valid,:3]=targets[indices][inverse]
    if not np.array_equal(a[...,3],np.asarray(legacy.convert('RGBA'))[...,3]):raise ValueError('Legacy alpha changed')
    report.update(legacy_colors=len(colors),mean_normal_rgb_error=float(np.average(errors,weights=counts)),max_normal_rgb_error=float(errors.max()),alpha_unchanged=True,all_output_visible_rgb_from_shiny_source=True)
    return Image.fromarray(a),report
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--recipe',required=True);ap.add_argument('--output',required=True);ap.add_argument('--report',required=True);a=ap.parse_args();recipe_path=(ROOT/a.recipe).resolve();output=(ROOT/a.output).resolve();report=(ROOT/a.report).resolve()
    if any(not p.is_relative_to(ROOT/'builds') for p in (recipe_path,output,report)) or output.exists() or report.exists():raise ValueError('Explicit project recipe and fresh outputs required')
    recipe=json.loads(recipe_path.read_text());images=[]
    for key in ('legacy','normal','shiny'):
        row=recipe[key];file=(ROOT/row['file']).resolve()
        if not file.is_relative_to(ROOT/'builds') or hashlib.sha256(file.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Pinned source changed')
        images.append(Image.open(file).convert('RGBA'))
    converted,evidence=transfer(*images,alpha_is_data=recipe.get('alpha_is_data',False))
    if evidence['mean_normal_rgb_error']>recipe.get('max_mean_rgb_error',12):raise ValueError('Source palette is too different for an exact-species compatibility candidate')
    converted.save(output);evidence.update(recipe=a.recipe,output=a.output,sha256=hashlib.sha256(output.read_bytes()).hexdigest(),acceptance='Compatibility palette conversion only; native visual review required');report.write_text(json.dumps(evidence,indent=2)+'\n');print(json.dumps(evidence))
if __name__=='__main__':main()
