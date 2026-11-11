"""Record actual UI executions with explicitly synthetic narration. Requires ffmpeg/flite."""
import base64
import json
import os
import subprocess
import time
import wave
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
TEMP=ROOT/'test-results'/'recording'; TEMP.mkdir(parents=True,exist_ok=True)
scenes=[
('intro','This is Vendor Flow, a working vendor onboarding process for case study two. This demonstration uses synthetic data and synthesized narration. The dashboard starts empty. Each submission runs real checks, saves its reasoning, and produces an approved, pending, or rejected decision.'),
('form','Here is a complete vendor submission. It includes company identity, a U S tax number, contact details, bank information, and supporting evidence. Documents can be uploaded as text PDFs or text files. These examples use structured evidence exports. The system never guesses missing document fields.'),
('approved','The submission is approved under internal policy. All six stages have actually executed. The tax and bank evidence match the form. The routing checksum is valid, and the compliance declaration is current. Every check has a reason. Approval does not establish document authenticity or authorize a payment.'),
('missing','Now a different case: missing tax evidence and an expired compliance declaration. The process returns pending. The follow up explains precisely what to provide. The message is available in the result and as a download. No external email is sent to an unverified address.'),
('owner','This case has a subtle cross field mismatch. The submitted bank holder agrees with the bank document. However, that holder is a different legal entity from the vendor. The workflow requests clarification instead of accepting consistent but unsafe payment details.'),
('routing','Here the form and bank evidence agree, but the routing number fails its mathematical checksum. The status is pending with a concrete correction request. Matching documents alone are insufficient to approve the submission.'),
('shared','Finally, Cedar Logistics submits internally consistent evidence, but reuses a bank account already approved for a different tax identity. The historical cross check catches this and rejects the submission for investigation. Rejection takes precedence over pending findings.'),
('dashboard','The dashboard now contains five actual runs: one approved, three pending, and one rejected. Each can be reopened to inspect its audit trail and download outputs. History survives server restarts. The policy is deterministic, with no paid A I service required. Bank verification, document authentication, and external sanctions screening remain explicit future integrations.')]
audio=[]
for i,(key,text) in enumerate(scenes):
    textfile=TEMP/f'{i}.txt'; textfile.write_text(text)
    wav=TEMP/f'{i}.wav'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i',f'flite=textfile={textfile}:voice=slt','-ar','48000','-ac','1',str(wav)],check=True)
    with wave.open(str(wav)) as f: raw=f.readframes(f.getnframes()); duration=f.getnframes()/f.getframerate()
    audio.append((raw,duration))
segments=[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page(); cdp=context.new_cdp_session(page); frames=[]
    start=time.monotonic()
    def frame(event):
        path=TEMP/f'frame-{len(frames):05d}.jpg';path.write_bytes(base64.b64decode(event['data']))
        frames.append((time.monotonic()-start,path))
        cdp.send('Page.screencastFrameAck',{'sessionId':event['sessionId']})
    cdp.on('Page.screencastFrame',frame)
    cdp.send('Page.startScreencast',{'format':'jpeg','quality':85,'maxWidth':1440,'maxHeight':1000})
    page.goto(os.environ.get('BASE_URL','http://127.0.0.1:8001'))
    page.locator('#metric-total').filter(has_text='0').wait_for()
    def narrate(index):
        raw,duration=audio[index];segments.append((time.monotonic()-start,raw));page.wait_for_timeout((duration+.7)*1000)
    def scenario(name,status):
        page.locator('#new-submission').click()
        page.get_by_role('button',name=name+' ↗',exact=False).click()
        page.locator('#submit-button').click()
        page.locator('#run-status').filter(has_text=status).wait_for()
    narrate(0)
    page.locator('#new-submission').click();page.get_by_role('button',name='Complete submission ↗',exact=False).click();narrate(1)
    page.locator('#submit-button').click();page.locator('#run-status').filter(has_text='approved').wait_for();narrate(2)
    page.locator('#run-back').click();scenario('Missing & expired evidence','pending');narrate(3)
    page.locator('#run-back').click();scenario('Third-party payee','pending');narrate(4)
    page.locator('#run-back').click();scenario('Invalid routing checksum','pending');narrate(5)
    page.locator('#run-back').click();scenario('Shared account','rejected');narrate(6)
    page.locator('#run-back').click();page.locator('#metric-total').filter(has_text='5').wait_for();narrate(7)
    cdp.send('Page.stopScreencast'); end=time.monotonic()-start;context.close();browser.close()
    source=TEMP/'frames.ffconcat'
    lines=['ffconcat version 1.0']
    for i,(at,path) in enumerate(frames):
        until=frames[i+1][0] if i+1<len(frames) else end
        lines += [f"file '{path}'", f'duration {max(.04,until-at):.4f}']
    lines += [f"file '{frames[-1][1]}'"]
    source.write_text('\n'.join(lines)+'\n')
with wave.open(str(TEMP/'narration.wav'),'wb') as out:
    out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000)
    cursor=0
    for at,raw in segments:
        target=int(at*48000)
        out.writeframes(b'\0\0'*max(0,target-cursor));out.writeframes(raw);cursor=max(cursor,target)+len(raw)//2
subprocess.run(['ffmpeg','-y','-loglevel','error','-safe','0','-i',str(source),'-i',str(TEMP/'narration.wav'),'-vf','fps=15,tpad=stop_mode=clone:stop_duration=30','-c:v','libx264','-preset','fast','-crf','27','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-shortest','-t',str(cursor/48000),'-movflags','+faststart',str(ROOT/'docs'/'demo.mp4')],check=True)
print('Recorded docs/demo.mp4 from real UI executions; narration is synthesized.')
