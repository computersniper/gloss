// Synthetic PDFs and mocked APIs only. No paid request or computer API.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || process.env.LOCAL_APP_URL || 'http://127.0.0.1:5189/?mode=local';
function pdf(title, width) {
  const stream = `BT /F1 24 Tf 35 720 Td (${title}) Tj ET 35 650 ${width} 30 re f`;
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>', '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`];
  let body='%PDF-1.4\n'; const offsets=[];
  objects.forEach((o,i)=>{offsets.push(Buffer.byteLength(body));body+=`${i+1} 0 obj\n${o}\nendobj\n`;});
  const xref=Buffer.byteLength(body); body+=`xref\n0 6\n0000000000 65535 f \n${offsets.map(o=>String(o).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`; return Buffer.from(body);
}
async function rendered(p,title){
  await p.waitForFunction(title=>{const c=document.querySelector('.ml-reader canvas');if(!c?.getAttribute('aria-label')?.includes(title)||!c.width)return false;const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let ink=0;for(let i=0;i<d.length;i+=4)if(d[i+3]&&d[i]<180&&d[i+1]<180&&d[i+2]<180)ink++;return ink>100;},title,{timeout:15000}).catch(async e=>{console.error("render failure",title,await p.locator(".ml-content").innerText());throw e;});
  return p.locator('.ml-reader canvas').evaluate(c=>c.toDataURL());
}
(async()=>{
  const b=await chromium.launch({executablePath:process.env.BROWSER_PATH||undefined});
  try{for(const viewport of [{width:390,height:844},{width:820,height:1180}]){
    const context=await b.newContext({viewport}), p=await context.newPage(),errors=[],calls=[],computer=[];let status=200;
    p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))computer.push(r.url());});
    await p.route('https://api.openai.com/v1/chat/completions',async r=>{calls.push(r.request().postDataJSON());await r.fulfill({status,contentType:'application/json',body:JSON.stringify({choices:[{message:{content:'Mock paper answer'}}]})});});
    console.log("starting",viewport.width); await p.goto(base);const key=p.getByLabel('API Key',{exact:true}),question=p.getByLabel('问题',{exact:true}),send=p.getByRole('button',{name:'发送',exact:true});
    await key.fill('fake-test-key');await question.fill('Explain my paper');await send.click();await p.getByText('Mock paper answer',{exact:true}).waitFor();
    assert.equal(calls[0].stream,false);assert(!(await p.evaluate(()=>localStorage.getItem('glossx.mobile.local.v1'))).includes('fake-test-key'));
    await p.getByRole('button',{name:'归档对话',exact:true}).click();await p.getByRole('button',{name:'归档',exact:true}).click();await p.getByRole('button',{name:'恢复对话',exact:true}).click();await p.getByRole('button',{name:'返回对话',exact:true}).click();await p.getByRole('button',{name:'Explain my paper',exact:true}).click();await p.reload();assert.equal(await key.inputValue(),'');await p.getByText('Mock paper answer',{exact:true}).waitFor();
    console.log("chat passed",viewport.width); const upload=p.locator('input[type=file]'),select=p.getByRole('combobox',{name:'选择本地论文',exact:true});
    await upload.setInputFiles({name:'A.pdf',mimeType:'application/pdf',buffer:pdf('Local paper A evidence',80)});const a=await rendered(p,'A.pdf');console.log('PDF A rendered');
    await upload.setInputFiles({name:'B.pdf',mimeType:'application/pdf',buffer:pdf('Local paper B evidence',280)});const bb=await rendered(p,'B.pdf');console.log('PDF B rendered');assert.notEqual(a,bb);
    for(let i=0;i<4;i++){await select.selectOption({label:'A.pdf'});assert.equal(await rendered(p,'A.pdf'),a);await select.selectOption({label:'B.pdf'});assert.equal(await rendered(p,'B.pdf'),bb);}
    await select.selectOption({label:'A.pdf'});await key.fill('fake-test-key');await p.getByRole('checkbox',{name:'问答时发送当前论文的前 24000 字符',exact:true}).check();await question.fill('Read the evidence');await send.click();await p.getByText('Mock paper answer',{exact:true}).nth(1).waitFor();assert(calls.at(-1).messages[0].content.includes('Local paper A evidence'));assert(!calls.at(-1).messages[0].content.includes('Local paper B evidence'));
    await p.reload();await p.waitForFunction(()=>document.querySelector('[aria-label="选择本地论文"]').options.length===3);await select.selectOption({label:'B.pdf'});assert.equal(await rendered(p,'B.pdf'),bb);
    await key.fill('fake-persistent-key');const remember=p.getByRole('checkbox',{name:'在此设备保存密钥（明文，仅限可信设备）',exact:true});await remember.check();await p.waitForFunction(()=>JSON.parse(localStorage.getItem('glossx.mobile.local.v1')).settings.apiKey==='fake-persistent-key');await p.reload();await p.getByRole('button',{name:'API 设置',exact:true}).click();assert.equal(await key.inputValue(),'fake-persistent-key');await remember.uncheck();await p.waitForFunction(()=>JSON.parse(localStorage.getItem('glossx.mobile.local.v1')).settings.apiKey==='');await p.reload();assert.equal(await key.inputValue(),'');await key.fill('fake-key');
    status=401;await question.fill('Trigger mock failure');await send.click();await p.getByRole('alert').filter({hasText:'HTTP 401'}).waitFor();await question.fill('Can retry');assert.equal(await send.isDisabled(),false);
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));assert.deepEqual(errors,[]);assert.deepEqual(computer,[]);console.log(`PASS ${viewport.width}x${viewport.height}: PDF bitmaps/switches, extracted API context, IndexedDB reload, key opt-in/clear, archive restore, HTTP 401 recovery`);await context.close();
  }}finally{await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
