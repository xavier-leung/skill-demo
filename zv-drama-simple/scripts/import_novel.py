"""Normalize novel input into one UTF-8 Markdown source, with an explicit content check."""
import argparse
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from core import atomic_text, file_digest, local_path, lock, now, read_json, write_json


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.hidden+=1
        if self.hidden:return
        if tag in ('p','div','section','article','li','tr','br'):self.parts.append('\n\n')
        if tag in ('h1','h2','h3','h4','h5','h6'):self.parts.append('\n\n'+'#'*int(tag[1])+' ')
        if tag in ('td','th'):self.parts.append(' | ')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):
            self.hidden=max(0,self.hidden-1);return
        if not self.hidden and tag in ('p','div','section','article','li','tr','h1','h2','h3','h4','h5','h6'):self.parts.append('\n\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def decode(raw,encoding=None):
    if encoding:return raw.decode(encoding,errors='strict')
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):return raw.decode('utf-16')
    for name in ('utf-8-sig','gb18030'):
        try:return raw.decode(name,errors='strict')
        except UnicodeError:pass
    raise ValueError('无法可靠识别文本编码，请指定 --encoding；禁止用替换字符吞掉正文')


def html_md(text):
    parser=HTMLText();parser.feed(text)
    return ''.join(parser.parts)


def normalize(text):
    text=text.replace('\r\n','\n').replace('\r','\n').strip()
    if not text or '\x00' in text or '\ufffd' in text:
        raise ValueError('转换结果为空或含无效字符，不能作为小说正文')
    return text+'\n'


def extract(path,encoding=None):
    suffix=path.suffix.lower()
    if suffix in ('.md','.markdown','.txt','.text'):
        return decode(path.read_bytes(),encoding),'text',[]
    if suffix in ('.html','.htm','.xhtml'):
        return html_md(decode(path.read_bytes(),encoding)),'html', ['检查网页是否包含导航、广告或缺失分页正文']
    if suffix=='.docx':
        with zipfile.ZipFile(path) as z:
            root=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs=[]
        for p in root.findall('.//w:body//w:p',ns):
            line=[]
            for node in p.iter():
                tag=node.tag.rsplit('}',1)[-1]
                if tag=='t':line.append(node.text or '')
                elif tag in ('br','cr'):line.append('\n')
                elif tag=='tab':line.append('\t')
            paragraphs.append(''.join(line))
        warnings=['核对章节顺序、表格和修订内容；正文中的图片文字需另做 OCR']
        return '\n\n'.join(paragraphs),'docx-xml',warnings
    if suffix=='.epub':
        with zipfile.ZipFile(path) as z:
            container=ET.fromstring(z.read('META-INF/container.xml'))
            opf=next(x.attrib['full-path'] for x in container.iter() if x.tag.endswith('rootfile'))
            package=ET.fromstring(z.read(opf));base=posixpath.dirname(opf)
            items={x.attrib['id']:x.attrib for x in package.iter() if x.tag.endswith('}item')}
            ids=[x.attrib['idref'] for x in package.iter() if x.tag.endswith('}itemref')]
            if not ids:raise ValueError('EPUB 缺少阅读顺序，不能猜测文件排序')
            pieces=[]
            for id in ids:
                item=items[id]
                if item.get('media-type') not in ('application/xhtml+xml','text/html'):
                    raise ValueError('EPUB 含非文本正文，需先用合适工具提取或 OCR')
                name=posixpath.normpath(posixpath.join(base,item['href'].split('#')[0]))
                from urllib.parse import unquote
                pieces.append(html_md(decode(z.read(unquote(name)),encoding)))
        return '\n\n'.join(pieces),'epub-spine',['核对目录、首尾章节及书内插图文字']
    if suffix=='.pdf' and shutil.which('pdftotext'):
        result=subprocess.run(['pdftotext','-enc','UTF-8',str(path),'-'],capture_output=True,timeout=180)
        if result.returncode:raise ValueError('PDF 提取失败，请使用可用文档工具或 OCR')
        return result.stdout.decode('utf-8'),'pdftotext',['逐页核对缺页、分栏顺序和扫描页；文本层不保证完整']
    if suffix in ('.odt','.rtf','.fb2','.rst','.tex') and shutil.which('pandoc'):
        result=subprocess.run(['pandoc',str(path),'-t','gfm','--wrap=none'],capture_output=True,timeout=180)
        if result.returncode:raise ValueError('文档转换失败，请使用对应读取工具')
        return result.stdout.decode('utf-8'),'pandoc',['核对正文、脚注、章节顺序和插图文字']
    raise ValueError('此格式需要相应文档读取/OCR工具。先提取完整 Markdown，再用 --converted 登记；不得只改扩展名或以摘要代替正文')


def import_novel(project,source,encoding=None,converted=None):
    root=Path(project).resolve();source=Path(source).resolve()
    if not source.is_file():raise ValueError('输入文件不存在')
    with lock(root):
        target=local_path(root,'source/novel.md',exists=False)
        manifest=local_path(root,'source/conversion.json',exists=False)
        if target.exists() or manifest.exists():raise ValueError('已存在规范小说；保留现有版本，修订请修改规范 MD 后重新 verify，不自动覆盖')
        if converted:
            md=Path(converted)
            if md.suffix.lower()!='.md':raise ValueError('--converted 需要完整 UTF-8 Markdown 文件')
            text=md.read_text(encoding='utf-8');method='external-extraction';warnings=['人工核对外部提取/OCR结果，包括完整性和识别错误']
        else:text,method,warnings=extract(source,encoding)
        text=normalize(text)
        source_hash=file_digest(source)
        archive=local_path(root,'source/originals/'+source_hash[:16]+source.suffix.lower(),exists=False)
        archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists():shutil.copyfile(source,archive)
        atomic_text(target,text)
        report={'schemaVersion':1,'canonicalFile':'source/novel.md','originalName':source.name,
                'archive':str(archive.relative_to(root)),'originalSha256':source_hash,'method':method,
                'canonicalSha256':file_digest(target),'characters':len(text),'warnings':warnings,
                'verified':False,'reviewNotes':'待核对正文完整性、顺序与编码','createdAt':now()}
        write_json(manifest,report)
        return report


def verify(project,notes):
    if not notes.strip():raise ValueError('请记录实际核对方式与发现，不能空确认')
    with lock(project):
        path=local_path(project,'source/conversion.json');report=read_json(path)
        target=local_path(project,'source/novel.md')
        text=target.read_text(encoding='utf-8');normalize(text)
        report.update({'canonicalSha256':file_digest(target),'characters':len(text),'verified':True,'reviewNotes':notes,'verifiedAt':now()})
        write_json(path,report);return report


def canonical_source(project,brief):
    if brief['sourceFile']!='source/novel.md':raise ValueError('小说参考必须为 source/novel.md，先统一转换为 Markdown')
    source=local_path(project,'source/novel.md')
    report=read_json(local_path(project,'source/conversion.json'))
    if report.get('canonicalFile')!='source/novel.md' or report.get('verified') is not True or report.get('canonicalSha256')!=file_digest(source):
        raise ValueError('规范 Markdown 尚未核对或已经变化，请重新核对并 verify')
    text=source.read_text(encoding='utf-8');normalize(text)
    return text


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('import');a.add_argument('project');a.add_argument('source');a.add_argument('--encoding');a.add_argument('--converted')
    a=sub.add_parser('verify');a.add_argument('project');a.add_argument('--notes',required=True)
    args=p.parse_args()
    try:
        result=import_novel(args.project,args.source,args.encoding,args.converted) if args.command=='import' else verify(args.project,args.notes)
        import json
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,StopIteration,zipfile.BadZipFile,ET.ParseError,subprocess.TimeoutExpired) as exc:p.exit(1,str(exc)+'\n')
