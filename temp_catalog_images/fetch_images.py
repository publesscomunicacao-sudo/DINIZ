import re, json, csv, time, io, unicodedata, urllib.parse, hashlib, difflib
from pathlib import Path
from collections import Counter
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageDraw

ROOT=Path('temp_catalog_images'); OUT=Path('catalog_images_248'); IMGDIR=OUT/'imagens'
OUT.mkdir(exist_ok=True); IMGDIR.mkdir(exist_ok=True)
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36'
S=requests.Session(); S.headers.update({'User-Agent':UA,'Accept-Language':'pt-BR,pt;q=0.9,en;q=0.7'})
TIMEOUT=16
products=[]
for fp in sorted(ROOT.glob('products_part*.json')):
    for pid,name,cat,url in json.load(open(fp,encoding='utf-8')):products.append({'id':pid,'product':name,'category':cat,'url':url})
products.sort(key=lambda x:int(x['id']));assert len(products)==248,len(products)

def ascii_text(s):return unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode()
def slug(s):return (re.sub(r'[^A-Za-z0-9]+','_',ascii_text(s)).strip('_')[:90] or 'produto')
def normtxt(s):return re.sub(r'[^a-z0-9]+',' ',ascii_text(s).lower()).strip()
def absu(base,u):
    if not u:return None
    u=u.replace('\\/','/')
    if u.startswith('//'):return 'https:'+u
    return urllib.parse.urljoin(base,u)
def clean(name):return re.sub(r'\(\s*\d+\s*(?:Unid|Uni|Und)\s*\)','',name,flags=re.I).strip()
def codes(name):return list(dict.fromkeys(re.findall(r'\b(?:[A-Z]{1,4}-?[A-Z]?\d{2,4}[A-Z]?|[A-Z]{1,3}\d{2,4}-[A-Z0-9-]+|\d{4})\b',ascii_text(name).upper())))

def extract_images(url):
    c=[]
    try:
        r=S.get(url,timeout=TIMEOUT,allow_redirects=True)
        if r.status_code!=200:return [],f'http_{r.status_code}',r.url,''
        soup=BeautifulSoup(r.text,'html.parser');title=soup.title.get_text(' ',strip=True) if soup.title else ''
        for tag,attrs,att in [('meta',{'property':'og:image'},'content'),('meta',{'property':'og:image:secure_url'},'content'),('meta',{'name':'twitter:image'},'content'),('meta',{'name':'twitter:image:src'},'content'),('link',{'rel':'image_src'},'href')]:
            for x in soup.find_all(tag,attrs=attrs):
                if x.get(att):c.append(absu(r.url,x.get(att)))
        for sc in soup.find_all('script',type='application/ld+json'):
            try:
                stack=[json.loads(sc.string or sc.get_text() or '{}')]
                while stack:
                    v=stack.pop()
                    if isinstance(v,dict):
                        im=v.get('image');ims=im if isinstance(im,list) else [im] if im else []
                        for q in ims:
                            if isinstance(q,str):c.append(absu(r.url,q))
                            elif isinstance(q,dict):
                                for k in ('url','contentUrl'):
                                    if q.get(k):c.append(absu(r.url,q[k]))
                        stack.extend(v.values())
                    elif isinstance(v,list):stack.extend(v)
            except Exception:pass
        for pat in [r'https?:\\?/\\?/[^"\'<>\s]+?\.(?:jpe?g|png|webp|avif)(?:\?[^"\'<>\s]*)?',r'https?:%2F%2F[^"\'<>\s]+?\.(?:jpe?g|png|webp|avif)(?:%3F[^"\'<>\s]*)?']:
            for m in re.findall(pat,r.text,re.I):c.append(urllib.parse.unquote(m).replace('\\/','/'))
        for tag in soup.find_all(['img','source']):
            for a in ('src','data-src','data-lazy-src','data-original'):
                if tag.get(a):c.append(absu(r.url,tag.get(a)))
            for a in ('srcset','data-srcset'):
                if tag.get(a):
                    ents=[]
                    for part in tag.get(a).split(','):
                        bits=part.strip().split()
                        if bits:
                            try:score=int(re.sub(r'\D','',bits[1]) or 0) if len(bits)>1 else 0
                            except:score=0
                            ents.append((score,absu(r.url,bits[0])))
                    c.extend(u for _,u in sorted(ents,reverse=True))
        bad=('logo','favicon','icon','avatar','placeholder','sprite','manifest','payment','banner','pixel','tracking')
        out=[];seen=set()
        for u in c:
            if not u or u in seen:continue
            seen.add(u)
            if any(b in u.lower() for b in bad):continue
            out.append(u)
        return out,'ok',r.url,title
    except Exception as e:return [],f'error:{type(e).__name__}',url,''

def candidate_text(a):
    bits=[a.get_text(' ',strip=True),a.get('title','')]
    im=a.find('img')
    if im:bits += [im.get('alt',''),im.get('title','')]
    par=a.parent
    if par:bits.append(par.get_text(' ',strip=True)[:450])
    return ' '.join(x for x in bits if x)
STOP={'de','do','da','em','com','sem','para','pra','c','unid','und','uni','display','1','24','25','20','10','8','6','3','2'}
def sim(target,cand):
    t=normtxt(target);c=normtxt(cand)
    if not t or not c:return 0
    tt=[x for x in t.split() if x not in STOP];ct=set(c.split());over=sum(1 for x in tt if x in ct)/max(1,len(tt))
    ratio=difflib.SequenceMatcher(None,t,c).ratio();bonus=0
    for cd in codes(target):
        if normtxt(cd) in c:bonus+=.55
        else:bonus-=.15
    brand=tt[1] if len(tt)>1 else tt[0] if tt else ''
    if brand and brand in ct:bonus+=.08
    return ratio*.25+over*.65+bonus

def query_variants(name):
    n=clean(name);out=[]
    for cd in codes(n):out.append(cd)
    out.append(n)
    words=[w for w in re.split(r'\s+',n) if w and normtxt(w) not in STOP]
    if len(words)>7:out.append(' '.join(words[:7]))
    if len(words)>4:out.append(' '.join(words[:5]))
    return list(dict.fromkeys(out))

def search_supplier(name):
    sites=[('atacado_dos_crias','https://www.atacadodoscrias.com.br/loja/busca.php','1419272'),('one_atacadista','https://www.oneatacadista.com.br/loja/busca.php','693228'),('one_tabacaria','https://www.onetabacaria.com.br/loja/busca.php','693228')]
    best=None
    for st,base,loja in sites:
        for q in query_variants(name):
            try:r=S.get(base,params={'loja':loja,'palavra_busca':q},timeout=TIMEOUT,allow_redirects=True)
            except Exception:continue
            if r.status_code!=200:continue
            soup=BeautifulSoup(r.content,'html.parser')
            seen=set()
            for a in soup.find_all('a',href=True):
                href=absu(r.url,a.get('href'));txt=candidate_text(a)
                if not href or href in seen or not txt:continue
                seen.add(href);lh=href.lower()
                if 'busca.php' in lh or href.rstrip('/')==r.url.rstrip('/'):continue
                sc=sim(name,txt)
                if sc<.36:continue
                if best is None or sc>best[0]:best=(sc,href,txt,st,q)
            if best and best[0]>=.82:break
        if best and best[0]>=.82:break
    if not best:return [],'',0,'',''
    sc,page,matched,st,q=best;imgs,note,final,title=extract_images(page)
    return imgs,final,sc,st,matched

def bing(q):
    out=[]
    try:
        r=S.get('https://www.bing.com/images/search',params={'q':q,'form':'HDRSC2','first':'1','tsc':'ImageBasicHover'},timeout=TIMEOUT);soup=BeautifulSoup(r.text,'html.parser')
        for a in soup.select('a.iusc'):
            try:
                d=json.loads(a.get('m') or '{}')
                if d.get('murl'):out.append((d['murl'],d.get('purl','')))
            except Exception:pass
    except Exception:pass
    return out[:30]
def fetch(u,ref=''):
    hdr={'Referer':ref} if ref else {}
    for tu in [u,'https://images.weserv.nl/?url='+urllib.parse.quote(u,safe='')+'&output=jpg&q=92']:
        try:
            r=S.get(tu,headers=hdr,timeout=TIMEOUT,allow_redirects=True)
            if r.status_code==200 and len(r.content)>=4000:return r.content
        except Exception:pass
    return None
def norm_image(data):
    try:
        im=Image.open(io.BytesIO(data));im.load();im=ImageOps.exif_transpose(im)
        if im.width<180 or im.height<180:return None
        if max(im.width/im.height,im.height/im.width)>5.5:return None
        if im.mode in ('RGBA','LA'):
            bg=Image.new('RGB',im.size,'white');bg.paste(im,mask=im.getchannel('A'));im=bg
        else:im=im.convert('RGB')
        if max(im.size)>2200:im.thumbnail((2200,2200),Image.Resampling.LANCZOS)
        b=io.BytesIO();im.save(b,'JPEG',quality=92,optimize=True);return b.getvalue(),im.size
    except Exception:return None

def choose_images(candidates,ref):
    tried=0
    for u in candidates:
        tried+=1;data=fetch(u,ref)
        if not data:continue
        n=norm_image(data)
        if n:return u,n,tried
    return None,None,tried

manifest=[];hashes={}
for i,p in enumerate(products,1):
    pid,name,cat,url=p['id'],p['product'],p['category'],p['url'];chosen=None;tried=0;source='';matched='';score=0;note=''
    if url:
        imgs,note,page,title=extract_images(url);iu,n,tt=choose_images(imgs,page);tried+=tt
        if n:chosen=(iu,n);source='kyte_planilha';page_src=page;score=1;matched=title
    else:
        imgs,page_src,score,source,matched=search_supplier(name);iu,n,tt=choose_images(imgs,page_src);tried+=tt
        if n:chosen=(iu,n)
    if not chosen:
        for q in [f'"{clean(name)}"',f'{clean(name)} produto tabacaria',f'{clean(name)} Brasil']:
            for u,src in bing(q):
                tried+=1;data=fetch(u,src)
                if not data:continue
                n=norm_image(data)
                if n:chosen=(u,n);page_src=src;source='bing';score=0;matched=q;break
            if chosen:break
    if chosen:
        iu,(jpg,size)=chosen;fn=f'{pid}_{slug(name)}.jpg';(IMGDIR/fn).write_bytes(jpg);h=hashlib.sha256(jpg).hexdigest();dup=hashes.get(h,'');hashes.setdefault(h,pid)
        if source=='kyte_planilha':status='imagem_da_url_da_planilha';conf='alta'
        elif source in ('atacado_dos_crias','one_atacadista','one_tabacaria'):status='imagem_fornecedor_revisar';conf='alta' if score>=.58 else 'media'
        else:status='imagem_web_revisar';conf='media'
        manifest.append([pid,name,cat,status,conf,source,iu,page_src,size[0],size[1],fn,tried,note,dup,round(score,3),matched[:260]])
    else:
        im=Image.new('RGB',(1200,1200),'white');d=ImageDraw.Draw(im);d.multiline_text((80,410),f'{pid}\n{name}\n\nIMAGEM NÃO LOCALIZADA\nCOM CONFIANÇA',fill='black',spacing=18);fn=f'{pid}_{slug(name)}_NAO_LOCALIZADA.jpg';im.save(IMGDIR/fn,'JPEG',quality=90)
        manifest.append([pid,name,cat,'nao_localizada','baixa','placeholder','','',1200,1200,fn,tried,note,'',0,''])
    print(f'[{i:03d}/248] {pid} {name[:43]} -> {manifest[-1][3]} / {manifest[-1][5]} / score {manifest[-1][-2]}',flush=True);time.sleep(.05)
headers=['ID','Produto','Categoria','Status','Confianca','Tipo_fonte','URL_imagem','Pagina_fonte','Largura','Altura','Arquivo','Tentativas','Nota','Duplicado_de_ID','Score_match','Texto_match']
with open(OUT/'manifesto.csv','w',newline='',encoding='utf-8-sig') as f:w=csv.writer(f);w.writerow(headers);w.writerows(manifest)
cols=5;rows=8;per=40
for si,start in enumerate(range(0,len(manifest),per),1):
    batch=manifest[start:start+per];canvas=Image.new('RGB',(1300,2280),'white');dr=ImageDraw.Draw(canvas)
    for j,row in enumerate(batch):
        pid,name,_,status,conf,source,_,_,_,_,fn,_,_,dup,score,_=row;im=Image.open(IMGDIR/fn).convert('RGB');im.thumbnail((220,220),Image.Resampling.LANCZOS);bx=(j%cols)*260;by=(j//cols)*285;canvas.paste(im,(bx+(260-im.width)//2,by+5));dr.text((bx+8,by+232),(pid+' '+name)[:34],fill='black');dr.text((bx+8,by+252),f'{source[:13]} {conf} s:{score}'+(f' DUP:{dup}' if dup else ''),fill='black')
    canvas.save(OUT/f'contato_{si:02d}.jpg','JPEG',quality=88)
c=Counter(r[3] for r in manifest);cf=Counter(r[4] for r in manifest);sources=Counter(r[5] for r in manifest);dups=sum(1 for r in manifest if r[13])
with open(OUT/'RESUMO.txt','w',encoding='utf-8') as f:
    f.write(f'Total de produtos: {len(manifest)}\nTotal de imagens: {len(list(IMGDIR.glob("*.jpg")))}\nDuplicadas detectadas: {dups}\n\nSTATUS\n');[f.write(f'{k}: {v}\n') for k,v in c.items()];f.write('\nFONTES\n');[f.write(f'{k}: {v}\n') for k,v in sources.items()];f.write('\nCONFIANÇA\n');[f.write(f'{k}: {v}\n') for k,v in cf.items()]
import shutil;shutil.make_archive('fotos_catalogo_mister_salve_248','zip',OUT);print('DONE',c,sources,cf,'dups',dups)
