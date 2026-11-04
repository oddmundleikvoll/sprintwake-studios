"""Prepare signed GitHub Releases assets locally; never upload anything."""
from __future__ import annotations
import json,plistlib,re,subprocess
from datetime import datetime,timezone
from pathlib import Path
from xml.etree import ElementTree as ET

SPARKLE='http://www.andymatuschak.org/xml-namespaces/sparkle'
ET.register_namespace('sparkle',SPARKLE)

def prepare_release(bundle,output,*,tools,account='sprintwake-studios-updates',asset_base=None,test_fixture=False,test_key_file=None):
    bundle=Path(bundle).resolve();output=Path(output).resolve();tools=Path(tools).resolve()
    info=plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    if not test_fixture and (info.get('CFBundleIdentifier')!='no.oddmund.storyboard.raven' or info.get('SprintwakeUpdateTest')):raise ValueError('Not a production Sprintwake Studios bundle')
    version=str(info['CFBundleShortVersionString']);build=str(info['CFBundleVersion'])
    if not re.fullmatch(r'\d+(?:\.\d+){1,3}',version) or not build.isdigit():raise ValueError('Invalid release version')
    release=json.loads((bundle/'Contents/Resources/release.json').read_text());repository=release['repository']
    if str(release['version'])!=version or str(release['build'])!=build:raise ValueError('Bundle and release metadata disagree')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise ValueError('Invalid GitHub repository')
    subprocess.run(['codesign','--verify','--deep','--strict',str(bundle)],check=True,capture_output=True)
    output.mkdir(parents=True,exist_ok=True)
    name=f'Sprintwake-Studios-{version}-arm64.zip';archive=output/name;feed=output/'appcast.xml'
    if archive.exists() or feed.exists():raise ValueError('Choose an empty output directory; existing release assets are preserved')
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(bundle),str(archive)],check=True)
    if test_key_file and not test_fixture:raise ValueError('File keys are restricted to disposable test releases')
    key_args=['--ed-key-file',str(test_key_file)] if test_key_file else ['--account',account]
    public=Path(str(test_key_file)+'.public').read_text().strip() if test_key_file else subprocess.run([str(tools/'generate_keys'),'--account',account,'-p'],check=True,capture_output=True,text=True).stdout.strip()
    if public!=info.get('SUPublicEDKey'):raise ValueError('Signing key does not match the bundled update key')
    sign=tools/'sign_update';result=subprocess.run([str(sign),*key_args,'-p',str(archive)],check=True,capture_output=True,text=True)
    signature=result.stdout.strip()
    subprocess.run([str(sign),*key_args,'--verify',str(archive),signature],check=True,capture_output=True)
    root=ET.Element('rss',{'version':'2.0'});channel=ET.SubElement(root,'channel');ET.SubElement(channel,'title').text='Sprintwake Studios'
    item=ET.SubElement(channel,'item');ET.SubElement(item,'title').text='Sprintwake Studios '+version
    ET.SubElement(item,f'{{{SPARKLE}}}version').text=build
    ET.SubElement(item,f'{{{SPARKLE}}}shortVersionString').text=version
    ET.SubElement(item,f'{{{SPARKLE}}}minimumSystemVersion').text=info.get('LSMinimumSystemVersion','14.0')
    ET.SubElement(item,f'{{{SPARKLE}}}hardwareRequirements').text='arm64'
    ET.SubElement(item,'pubDate').text=datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')
    base=asset_base or f'https://github.com/{repository}/releases/download/v{version}'
    if not test_fixture and not base.startswith(f'https://github.com/{repository}/releases/download/'):raise ValueError('Production assets must use the configured GitHub Releases repository')
    ET.SubElement(item,'enclosure',{'url':base.rstrip('/')+'/'+name,'length':str(archive.stat().st_size),'type':'application/octet-stream',f'{{{SPARKLE}}}edSignature':signature})
    ET.indent(root);ET.ElementTree(root).write(feed,encoding='utf-8',xml_declaration=True)
    subprocess.run([str(sign),*key_args,str(feed)],check=True,capture_output=True)
    subprocess.run([str(sign),*key_args,'--verify',str(feed)],check=True,capture_output=True)
    return {'archive':str(archive),'appcast':str(feed),'version':version,'build':build,'repository':repository,'uploaded':False}
