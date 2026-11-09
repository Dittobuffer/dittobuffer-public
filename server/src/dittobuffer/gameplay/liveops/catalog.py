"""Explicit task/reward capabilities and local model availability (API v1)."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from dittobuffer.paths import ROOT
from dittobuffer.core.asset_catalog import AssetStore

# These counters already flow through authoritative research.progress.
from dittobuffer.gameplay.research_task_registry import TASKS,UNAVAILABLE_TASKS

ITEMS={1:'Poke Ball',2:'Great Ball',3:'Ultra Ball',101:'Potion',102:'Super Potion',103:'Hyper Potion',104:'Max Potion',
       201:'Revive',202:'Max Revive',701:'Razz Berry',901:'Infinite Incubator',902:'Egg Incubator',
       1501:'Mysterious Component',1502:'Rocket Radar',1503:'Super Rocket Radar'}

class Catalog:
    def __init__(self,config,forms=None,species=None):
        if forms is None:
            from dittobuffer.gameplay.species import catalog,form_catalog
            forms=form_catalog(ROOT/'data/static/gmts.binpb.gz');species=catalog(ROOT/'data/static/gmts.binpb.gz')
        self.config=config;self.forms=forms;self.species=species;self.ready=set();self.shiny=set();self.error=None
        try:
            store=AssetStore(ROOT,config.get('pokemon_assets',{}));manifest=store.manifest
            if not manifest:raise ValueError('Restore the permitted Pokemon asset manifest first')
            for variant in manifest.get('variants',[]):
                if variant.get('missing_dependencies'):continue
                sid=variant.get('species');address=variant.get('address','')
                if sid not in species:continue
                suffix=address.split('.f',1)[1].split('.',1)[0] if '.f' in address else None
                candidates=[form for (s,form),rules in forms.items() if s==sid and (rules['name'].endswith('_'+suffix) or rules['name']==suffix)] if suffix else [species[sid]['form']]
                target=self.shiny if '.s' in address.split('.f',1)[0] or address.endswith('.s') or '.s.' in address else self.ready
                for form in candidates:target.add((sid,form))
            if not self.ready:raise ValueError('No evidenced model identities in restored registry')
        except (OSError,ValueError,KeyError) as error:self.error=str(error)

    def mon(self,entry,shiny=False):
        if not isinstance(entry,dict):raise ValueError('Pokemon selection must be an object')
        sid=entry.get('species',entry.get('id'))
        if type(sid) is not int or sid not in self.species:raise ValueError('Unsupported Pokemon species')
        form=entry.get('form',self.species[sid]['form'])
        if type(form) is not int or (sid,form) not in self.forms:raise ValueError('Unsupported species/form pair')
        if (sid,form) not in self.ready:raise ValueError(f'Missing verified local model for {sid}:{form}. Restore the required resources.')
        rate=entry.get('shiny_rate',0)
        if type(rate) not in (int,float) or not 0<=rate<=1:raise ValueError('Shiny rate must be between zero and one')
        if (shiny or rate>0) and (sid,form) not in self.shiny:raise ValueError(f'Missing shiny resources for {sid}:{form}')
        return self.forms[sid,form]

    def game(self):
        return SimpleNamespace(config=self.config,species=self.species,forms=self.forms,shiny_eligible=self.shiny)

    def rows(self):
        boundaries=[151,251,386,493,649,721,809,905,1025]
        return [dict(species=sid,name=rules['name'].replace('_',' ').title(),form=rules['form'],
          generation=next((i+1 for i,n in enumerate(boundaries) if sid<=n),9),types=rules['types'],
          family=rules['family'],special=rules.get('class',0),stats=sum(rules[k] for k in ('attack','defense','stamina')),
          available=(sid,rules['form']) in self.ready,shiny=(sid,rules['form']) in self.shiny,
          forms=[dict(id=f,name=v['name'],available=(sid,f) in self.ready,shiny=(sid,f) in self.shiny) for (s,f),v in self.forms.items() if s==sid])
          for sid,rules in sorted(self.species.items())]

    def capabilities(self):
        from dittobuffer.gameplay.special_encounters import registry
        return dict(version=1,tasks=[dict(id=k,label=v[0],native_type=v[1],supported=True) for k,v in TASKS.items()]+
             [dict(id=k,label=k.replace('_',' ').title(),supported=False,reason='No verified research counter adapter yet') for k in UNAVAILABLE_TASKS],
             rewards=[dict(id=k,label=v) for k,v in ITEMS.items() if k<1500 or self.config.get('rocket',{}).get('server_combat')],
             cinematics=[dict(id=k,species=v['species'],shiny=v['shiny_selector']) for k,v in registry().items()],
             level_research={'supported':True,'editable':False,'mode':'pinned-modern-gated','source':'level-research-reference.json','note':'Verified gate definitions are read-only; unverified charged-attack counter remains blocked.'},
             research_types=[dict(id=k,supported=k in ('field','special','timed','collection','branching','timed_branching','breakthrough')) for k in ('field','special','timed','collection','branching','timed_branching','breakthrough')],
             unavailable=['Shadow raids','featured evolution attacks','event medals','Safari Balls','Mighty Pokemon',
              'Global Challenges','cosmetic rewards','per-player raid boss changes'],
             released_max=deepcopy(self.config.get('max_system',{}).get('pools',[])),
             model_error=self.error)
