/**
 * Cross-tab TTS dedupe: several VTT tabs for one login must voice a reply once.
 */

function loadPlayerTab() {
  let player;
  jest.isolateModules(() => {
    require('./tts_player.js');
    player = window.TTSPlayer;
  });
  return player;
}

describe('TTSPlayer cross-tab claim', () => {
  let audioUrls;

  beforeEach(() => {
    jest.useFakeTimers();
    window.localStorage.clear();
    window.localStorage.setItem('n20.tts.autoplay', 'true');
    audioUrls = [];
    window.Audio = function (url) {
      audioUrls.push(url);
      this.play = () => Promise.resolve();
      this.pause = () => {};
      this.addEventListener = () => {};
      this.removeEventListener = () => {};
    };
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test('only one of two tabs plays the same reply', () => {
    const tabA = loadPlayerTab();
    const tabB = loadPlayerTab();
    const reply = { reply_id: 'r-1', audio_url: '/tts/r1.wav', speaker_name: 'Vampire' };

    tabA.playReplyAudio(reply);
    tabB.playReplyAudio(reply);
    jest.advanceTimersByTime(500);

    expect(audioUrls).toEqual(['/tts/r1.wav']);
  });

  test('a single tab still plays each reply', () => {
    const tab = loadPlayerTab();
    tab.playReplyAudio({ reply_id: 'r-2', audio_url: '/tts/r2.wav' });
    jest.advanceTimersByTime(500);
    expect(audioUrls).toEqual(['/tts/r2.wav']);
  });
});
