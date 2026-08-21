import re, json, csv, time, io, unicodedata, urllib.parse, hashlib
from pathlib import Path
from collections import Counter
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageDraw

ROOT=Path('temp_catalog_images'); OUT=Path('catalog_images_248'); IMGDIR=OUT/'imagens'
OUT.mkdir(exist_ok=True); IMGDIR.mkdir(exist_ok=True)
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36'
S=requests.Session(); S.headers.update({'User-Agent':UA,'Accept-Language':'pt-BR,pt;q=0.9,en;q=0.7'})
TIMEOUT=18
products=[]
for fp in sorted(ROOT.glob('products_part*.json')):
    for pid,name,cat,url in json.load(open(fp,encoding='utf-8')):
        products.append({'id':pid,'product':name,'category':cat,'url':url})
products.sort(key=lambda x:int(x['id'])); assert len(products)==248, len(products)

def slug(s):
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode(); s=re.sub(r'[^A-Za-z0-9]+','_',s).strip('_'); return s[:90] or 'produto'
def absu(base,u):
    if not u:return None
    u=u.replace('\\/','/')
    if u.startswith('//'): return 'https:'+u
    return urllib.parse.urljoin(base,u)
def kyte(url):
    c=[]
    try:
        r=S.get(url,timeout=TIMEOUT,allow_redirects=True)
        if r.status_code!=200:return [],f'http_{r.status_code}'
        soup=BeautifulSoup(r.text,'html.parser')
        for tag,attrs,att in [('meta',{'property':'og:image'},'content'),('meta',{'property':'og:image:secure_url'},'content'),('meta',{'name':'twitter:image'},'content'),('meta',{'name':'twitter:image:src'},'content'),('link',{'rel':'image_src'},'href')]:
            for x in soup.find_all(tag,attrs=attrs):
                if x.get(att):c.append(absu(r.url,x.get(att)))
        for sc in soup.find_all('script',type='application/ld+json'):
            try:
                stack=[json.loads(sc.string or sc.get_text() or '{}')]
                while stack:
                    v=stack.pop()
                    if isinstance(v,dict):
                        im=v.get('image')
                        ims=im if isinstance(im,list) else [im] if im else []
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
            for a in ('src','data-src','data-lazy-src'):
                if tag.get(a):c.append(absu(r.url,tag.get(a)))
            for a in ('srcset','data-srcset'):
                if tag.get(a):
                    ents=[]
                    for part in tag.get(a).split(','):
                        bits=part.strip().split()
                        if bits:
                            score=int(re.sub(r'\D','',bits[1]) or 0) if len(bits)>1 else 0
                            ents.append((score,absu(r.url,bits[0])))
                    c.extend(u for _,u in sorted(ents,reverse=True))
        bad=('logo','favicon','icon','avatar','placeholder','sprite','manifest','payment','banner')
        out=[];seen=set()
        for u in c:
            if not u or u in seen:continue
            seen.add(u)
            if any(b in u.lower() for b in bad):continue
            out.append(u)
        return out,'ok'
    except Exception as e:return [],f'error:{type(e).__name__}'

def bing(q):
    out=[]
    try:
        r=S.get('https://www.bing.com/images/search',params={'q':q,'form':'HDRSC2','first':'1','tsc':'ImageBasicHover'},timeout=TIMEOUT)
        soup=BeautifulSoup(r.text,'html.parser')
        for a in soup.select('a.iusc'):
            try:
                d=json.loads(a.get('m') or '{}')
                if d.get('murl'):out.append((d['murl'],d.get('purl','')))
            except Exception:pass
    except Exception:pass
    return out[:35]
def ddg(q):
    out=[]
    try:
        h=S.get('https://duckduckgo.com/',params={'q':q},timeout=TIMEOUT)
        m=re.search(r'vqd=["\']?([\d-]+)',h.text) or re.search(r'vqd=([\d-]+)&',h.text)
        if not m:return out
        r=S.get('https://duckduckgo.com/i.js',params={'l':'br-pt','o':'json','q':q,'vqd':m.group(1),'f':',,,','p':'1'},headers={'Referer':'https://duckduckgo.com/'},timeout=TIMEOUT)
        for x in r.json().get('results',[]):
            if x.get('image'):out.append((x['image'],x.get('url','')))
    except Exception:pass
    return out[:25]
def fetch(u,ref=''):
    hdr={'Referer':ref} if ref else {}
    for tu in [u,'https://images.weserv.nl/?url='+urllib.parse.quote(u,safe='')+'&output=jpg&q=92']:
        try:
            r=S.get(tu,headers=hdr,timeout=TIMEOUT,allow_redirects=True)
            if r.status_code==200 and len(r.content)>=4000:return r.content
        except Exception:pass
    return None
def norm(data):
    try:
        im=Image.open(io.BytesIO(data)); im.load(); im=ImageOps.exif_transpose(im)
        if im.width<180 or im.height<180:return None
        if max(im.width/im.height,im.height/im.width)>5.5:return None
        if im.mode in ('RGBA','LA'):
            bg=Image.new('RGB',im.size,'white');bg.paste(im,mask=im.getchannel('A'));im=bg
        else:im=im.convert('RGB')
        if max(im.size)>2200:im.thumbnail((2200,2200),Image.Resampling.LANCZOS)
        b=io.BytesIO();im.save(b,'JPEG',quality=92,optimize=True);return b.getvalue(),im.size
    except Exception:return None
def clean(name):return re.sub(r'\(\s*\d+\s*(?:Unid|Uni|Und)\s*\)','',name,flags=re.I).strip()

def score_search(name,cat):
    qs=[f'"{clean(name)}" produto',f'{clean(name)} {cat} headshop Brasil',f'{clean(name)} loja']
    out=[]
    for q in qs:
        out += [(u,src,'bing') for u,src in bing(q)]
        if len(out)>=18:break
    if not out:
        for q in qs[:2]:out += [(u,src,'duckduckgo') for u,src in ddg(q)]
    return out

manifest=[]; hashes={}
for i,p in enumerate(products,1):
    pid,name,cat,url=p['id'],p['product'],p['category'],p['url']; cand=[];note=''
    if url:
        kk,note=kyte(url);cand=[(u,url,'kyte') for u in kk]
    if not cand:cand=score_search(name,cat)
    chosen=None;tried=0
    for u,src,typ in cand:
        tried+=1;data=fetch(u,src or url)
        if not data:continue
        n=norm(data)
        if n:
            jpg,size=n;chosen=(u,src,typ,jpg,size);break
    if not chosen and url:
        for u,src,typ in score_search(name,cat):
            tried+=1;data=fetch(u,src)
            if not data:continue
            n=norm(data)
            if n:
                jpg,size=n;chosen=(u,src,typ,jpg,size);break
    if chosen:
        iu,src,typ,jpg,size=chosen;fn=f'{pid}_{slug(name)}.jpg';(IMGDIR/fn).write_bytes(jpg)
        h=hashlib.sha256(jpg).hexdigest();dup=hashes.get(h,'');hashes.setdefault(h,pid)
        if typ=='kyte':status='imagem_da_url_da_planilha';conf='alta'
        else:status='imagem_web_revisar';conf='media'
        manifest.append([pid,name,cat,status,conf,typ,iu,src,size[0],size[1],fn,tried,note,dup])
    else:
        im=Image.new('RGB',(1200,1200),'white');d=ImageDraw.Draw(im);d.multiline_text((80,410),f'{pid}\n{name}\n\nIMAGEM NÃO LOCALIZADA\nCOM CONFIANÇA',fill='black',spacing=18)
        fn=f'{pid}_{slug(name)}_NAO_LOCALIZADA.jpg';im.save(IMGDIR/fn,'JPEG',quality=90)
        manifest.append([pid,name,cat,'nao_localizada','baixa','placeholder','','',1200,1200,fn,tried,note,''])
    print(f'[{i:03d}/248] {pid} {name[:48]} -> {manifest[-1][3]}',flush=True);time.sleep(.08)
headers=['ID','Produto','Categoria','Status','Confianca','Tipo_fonte','URL_imagem','Pagina_fonte','Largura','Altura','Arquivo','Tentativas','Nota','Duplicado_de_ID']
with open(OUT/'manifesto.csv','w',newline='',encoding='utf-8-sig') as f:w=csv.writer(f);w.writerow(headers);w.writerows(manifest)
cols=5;rows=8;per=cols*rows
for si,start in enumerate(range(0,len(manifest),per),1):
    batch=manifest[start:start+per];canvas=Image.new('RGB',(1300,2280),'white');dr=ImageDraw.Draw(canvas)
    for j,row in enumerate(batch):
        pid,name,_,status,conf,_,_,_,_,_,fn,_,_,dup=row;im=Image.open(IMGDIR/fn).convert('RGB');im.thumbnail((220,220),Image.Resampling.LANCZOS)
        bx=(j%cols)*260;by=(j//cols)*285;x=bx+(260-im.width)//2;y=by+5;canvas.paste(im,(x,y));dr.text((bx+8,by+232),(pid+' '+name)[:34],fill='black');dr.text((bx+8,by+252),f'{status[:20]} {conf}'+(f' DUP:{dup}' if dup else ''),fill='black')
    canvas.save(OUT/f'contato_{si:02d}.jpg','JPEG',quality=88)
c=Counter(r[3] for r in manifest);cf=Counter(r[4] for r in manifest);dups=sum(1 for r in manifest if r[-1])
with open(OUT/'RESUMO.txt','w',encoding='utf-8') as f:
    f.write(f'Total de produtos: {len(manifest)}\nTotal de imagens: {len(list(IMGDIR.glob("*.jpg")))}\nDuplicadas detectadas: {dups}\n\nSTATUS\n');[f.write(f'{k}: {v}\n') for k,v in c.items()];f.write('\nCONFIANÇA\n');[f.write(f'{k}: {v}\n') for k,v in cf.items()]
import shutil;shutil.make_archive('fotos_catalogo_mister_salve_248','zip',OUT);print('DONE',c,cf,'dups',dups)
