/* Озвучка очереди WAV-файлами Enter через Web Audio API. */
(function () {
  const ROOT = '/sounds/ru-ru/';
  const NUMBER_FORM = 'b';
  const letterMap = {
    'А':'a','Б':'b','В':'v','Г':'g','Д':'d','Е':'e','Ё':'r_yo','Ж':'r_zh','З':'z','И':'i','Й':'r_i',
    'К':'k','Л':'l','М':'m','Н':'n','О':'o','П':'p','Р':'r','С':'s','Т':'t','У':'u','Ф':'f','Х':'h',
    'Ц':'r_c','Ч':'r_ch','Ш':'r_sh','Щ':'r_sch','Ы':'r_y','Э':'r_e','Ю':'r_yu','Я':'r_ya'
  };
  let audioContext = null;
  const bufferCache = new Map();

  function voiceRoot(gender) { return ROOT + (gender === 'male' ? 'male' : 'female') + '/'; }
  function numberFile(root, part) { return root + 'number/' + NUMBER_FORM + '-' + part + '.wav'; }
  function getAudioContext() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error('Web Audio API не поддерживается');
    if (!audioContext) audioContext = new AudioContextClass();
    return audioContext;
  }
  async function loadBuffer(path) {
    if (bufferCache.has(path)) return bufferCache.get(path);
    const response = await fetch(path);
    if (!response.ok) throw new Error(`Не удалось загрузить звук: ${path}`);
    const context = getAudioContext();
    const raw = await response.arrayBuffer();
    const buffer = await new Promise((resolve, reject) => context.decodeAudioData(raw, resolve, reject));
    bufferCache.set(path, buffer);
    return buffer;
  }
  async function playFile(path) {
    const context = getAudioContext();
    if (context.state === 'suspended') await context.resume();
    const buffer = await loadBuffer(path);
    await new Promise((resolve, reject) => {
      try {
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        source.onended = resolve;
        source.start(0);
      } catch (error) { reject(error); }
    });
  }
  function numberParts(number) {
    let value = Math.max(0, parseInt(number, 10) || 0);
    if (!value) return [];
    const parts = [];
    const hundreds = Math.floor(value / 100) * 100;
    if (hundreds) parts.push(hundreds);
    value %= 100;
    const tens = Math.floor(value / 10) * 10;
    if (tens) parts.push(tens);
    const ones = value % 10;
    if (ones) parts.push(ones);
    return parts;
  }
  function ticketParts(ticketCode, gender) {
    const code = String(ticketCode || '').toUpperCase();
    const match = code.match(/^(.*?)(\d+)$/);
    const prefix = match ? match[1] : code;
    const number = match ? match[2] : '';
    const root = voiceRoot(gender);
    const files = [];
    [...prefix].forEach(letter => {
      const fileName = letterMap[letter] || letter.toLowerCase();
      if (/^[a-z]$/.test(fileName) || letterMap[letter]) files.push(root + 'letter/' + fileName + '.wav');
    });
    if (number) numberParts(number).forEach(part => files.push(numberFile(root, part)));
    return files;
  }
  async function unlock() {
    try {
      const context = getAudioContext();
      if (context.state === 'suspended') await context.resume();
      await playFile('/sounds/gong/gong1.wav');
      return true;
    } catch (error) { return false; }
  }
  function prepare() {
    try {
      const context = getAudioContext();
      if (context.state === 'suspended') context.resume().catch(() => {});
    } catch (error) {}
  }
  async function announceTicket(ticketCode, windowName, options) {
    const settings = options || {};
    const root = voiceRoot(settings.gender);
    const files = [root + 'ticketprefix/posetitel_s_talonom.wav'];
    files.push(...ticketParts(ticketCode, settings.gender));
    files.push(root + 'windowprefix/k_oknu_nomer.wav');
    const windowNumber = String(windowName || '').match(/\d+/);
    if (windowNumber) numberParts(windowNumber[0]).forEach(part => files.push(numberFile(root, part)));
    if (settings.repeat) files.unshift('/sounds/gong/gong2.wav');
    else files.unshift('/sounds/gong/gong1.wav');
    try {
      for (const file of files) await playFile(file);
      return true;
    } catch (error) {
      return false;
    }
  }
  window.QueueSounds = {announceTicket, unlock, prepare};
})();
