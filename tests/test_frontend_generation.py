"""Exercise the 6/49 browser flow without a browser or payment service."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is needed to check browser JavaScript")
class FrontendGenerationTests(unittest.TestCase):
    def run_javascript(self, source):
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-"],
            input=source, text=True, capture_output=True, cwd=ROOT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generation_is_direct_and_recovers_after_failure(self):
        self.run_javascript(r"""
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html=fs.readFileSync('index.html','utf8');
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))
  if(match[1].trim())new vm.Script(match[1]);
const source=html.slice(html.indexOf('function readVars(){'),html.indexOf('function renderPrediction(d){'));
const elements=Object.fromEntries(['varN','varSeed','varExtra9','predStatus','predWarn','btnGen','btnPdf'].map(id=>[id,{value:'',style:{},disabled:false}]));
Object.assign(elements.varN,{value:'6'});Object.assign(elements.varSeed,{value:'649'});Object.assign(elements.varExtra9,{value:'1'});
const requests=[],rendered=[];let successful=true;
const context=vm.createContext({document:{getElementById:id=>elements[id]},URLSearchParams,PRED_API:'',lastPredData:null,
  PAYCFG:{payments_on:true,free_tickets:0},
  fetch:async(url,options)=>{requests.push({url,options});return {ok:successful,status:503,json:async()=>successful?{params:{n:Number(elements.varN.value)}}:{error:'Indisponibil'}};},
  renderPrediction:d=>rendered.push(d)});
vm.runInContext(source,context);
for(const n of [6,12]){
  elements.varN.value=String(n);await vm.runInContext('runPredict(false)',context);
  const url=new URL(requests.at(-1).url,'https://example.test');
  assert.equal(url.pathname,'/api/predict');
  assert.deepEqual([...url.searchParams.keys()],['n','seed','extra9']);
  assert.equal(url.searchParams.get('n'),String(n));
  assert.equal(elements.btnGen.disabled,false);
  assert.equal(elements.btnPdf.disabled,false);
}
assert.equal(requests.length,2);assert.equal(rendered.length,2);
await vm.runInContext('runPredict(true)',context);assert(requests.at(-1).url.includes('force=1'));
successful=false;await vm.runInContext('runPredict(false)',context);
assert.equal(elements.btnGen.disabled,false);assert.equal(rendered.length,3);assert(elements.predWarn.textContent.includes('Indisponibil'));
vm.runInContext('resetVars()',context);assert.equal(elements.varN.value,6);assert.equal(elements.varExtra9.value,1);assert.equal(elements.varSeed.value,649);
assert(!/Stripe|create-checkout|paid_session|PAYCFG/.test(html));
""")

    def test_v2_payload_renders_and_exports_without_legacy_fields(self):
        self.run_javascript(r"""
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html=fs.readFileSync('index.html','utf8');
class Element{
  constructor(){this.children=[];this.style={};this.classList={remove(){}};this.value='8';}
  set innerHTML(value){this.html=value;this.children=[...value.matchAll(/<td\b/g)].map(()=>new Element());}
  get innerHTML(){return this.html||'';}
  appendChild(child){this.children.push(child);}
}
const elements=new Map(),element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const tickets=Array.from({length:6},(_,i)=>({nums:Array.from({length:6},(_,j)=>i*6+j+1),sum:21+36*i,ip:'3/3',shape:'balanced'}));
const payload={engine:'649-coverage-v2',seed:649,params:{n:6,seed:649,extra9:1,extension_size:7,extension_variants:7,generation_mode:'uniform_coverage',cycle_date:'2026-10-25',disjoint_prefix:6},
  data:{total_draws:2596,first_date:'2000-01-01',last_date:'2026-10-25'},coverage:{distinct_numbers:36,unique_triples:120,max_overlap:0},
  tickets6:tickets,tickets9:tickets.map(t=>[...t.nums,49]),
  target_profile:{sum:'100-150',sum_p:.5,max:'40-49',max_p:.5,shape:'balanced',shape_p:.5,ip:'3/3',ip_p:.5},top_numbers:[{n:1,freq:1,delay:2}],top_pairs:[]};
const captured=[];
class PDF{
  internal={pageSize:{getWidth:()=>595,getHeight:()=>842},getNumberOfPages:()=>1};lastAutoTable={finalY:100};
  setFontSize(){}setTextColor(){}setFont(){}text(){}setDrawColor(){}setLineWidth(){}line(){}addPage(){}setPage(){}save(){}
  splitTextToSize(value){return[value];}autoTable(options){captured.push(options);}
}
const context=vm.createContext({document:{getElementById:element,createElement:()=>new Element(),querySelectorAll:()=>[]},fmt:String,SCHEME_CODE_BY_N:{7:'7'},lastPredData:payload,window:{jspdf:{jsPDF:PDF}}});
vm.runInContext(html.slice(html.indexOf('function renderPrediction(d){'),html.indexOf('// trimite un bilet generat')),context);
context.renderPrediction(payload);
assert.equal(element('predTickets').children.length,6);assert(element('predTickets').children.every(row=>row.children.length===4));
assert(element('predTickets').children.every(row=>row.children[3].children.length===3));
assert(element('predCoverage').innerHTML.includes('36 / 49'));assert(element('predCoverage').innerHTML.includes('2026-10-25'));
assert(element('predTopNums').children[0].innerHTML.includes('6/49'));
vm.runInContext(html.slice(html.indexOf('function exportPDF(){'),html.indexOf('// init\nupdateNeed();')),context);
context.exportPDF();assert(captured.some(table=>table.body.some(row=>row[0]==='Generare'&&row[1]==='Uniforma, fara ponderi istorice')));
assert(!html.includes('records649'));assert(!html.includes('predMarkov"'));
""")


if __name__ == "__main__":
    unittest.main()
