import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/ui/player/embed_frame.dart';

Uri aniBuild([String path = '/embed/ani/21/3/sub']) =>
    Uri.parse('https://anipm.pk$path');

void main() {
  group('buildEmbedFrameHtml', () {
    test('frames the embed full screen', () {
      final html = buildEmbedFrameHtml(aniBuild());
      expect(html, startsWith('<!doctype html>'));
      expect(html, contains('<iframe src="https://anipm.pk/embed/ani/21/3/sub"'));
      expect(html, contains('allowfullscreen'));
      expect(html, contains('position:fixed;inset:0;width:100%;height:100%'));
      expect(html, endsWith('</body></html>'));
    });

    test('allows what the provider documents', () {
      final html = buildEmbedFrameHtml(aniBuild());
      expect(
        html,
        contains(
          'allow="autoplay; fullscreen; picture-in-picture; '
          'encrypted-media; screen-wake-lock"',
        ),
      );
    });

    test('has no sandbox attribute', () {
      expect(buildEmbedFrameHtml(aniBuild()), isNot(contains('sandbox')));
    });

    test('the embed url cannot break out of the attribute', () {
      // `Uri` percent-encodes quotes and tags; a query's `&` still has to be
      // escaped by the builder itself.
      final html = buildEmbedFrameHtml(
        Uri.parse('https://anipm.pk/embed/ani/1/1/sub?a=1&b=2'),
      );
      expect(html, contains('?a=1&amp;b=2'));
      expect(html, isNot(contains('&b=2"')));
    });

    test('escaping handles every character that matters', () {
      expect(escapeHtmlAttribute('<&>"\''), '&lt;&amp;&gt;&quot;&#39;');
    });
  });

  group('needsEmbedFrame', () {
    test('ani only', () {
      expect(needsEmbedFrame('ani'), isTrue);
      expect(needsEmbedFrame(' ANI '), isTrue);
      for (final other in <String>[
        'cinesrc',
        'rivestream',
        'toflix',
        'cinelite',
        'framextv',
        'atlantic',
        'vidy',
        'chillflix',
        'vidcore',
        'velarium',
        'afterdusk',
        'moonflix',
        '',
      ]) {
        expect(needsEmbedFrame(other), isFalse, reason: other);
      }
    });
  });

  group('isEmbedFrameDocumentUrl', () {
    test('accepts the wrapper document and nothing else', () {
      expect(isEmbedFrameDocumentUrl(embedFrameBaseUrl), isTrue);
      expect(isEmbedFrameDocumentUrl('https://player.lumora.app'), isTrue);
      expect(
        isEmbedFrameDocumentUrl('https://player.lumora.app/other'),
        isFalse,
      );
      expect(
        isEmbedFrameDocumentUrl('https://anipm.pk/embed/ani/1/1/sub'),
        isFalse,
      );
      expect(
        isEmbedFrameDocumentUrl('https://player.lumora.app.evil/'),
        isFalse,
      );
      expect(isEmbedFrameDocumentUrl(''), isFalse);
    });
  });

  test('the base is a real https origin of its own', () {
    final base = Uri.parse(embedFrameBaseUrl);
    expect(base.scheme, 'https');
    expect(embedFrameBaseUrl, endsWith('/'));
    expect(embedFrameBaseUrl, isNot(contains('lumora.local')));
    expect(embedFrameBaseUrl, isNot(contains('anipm.pk')));
  });
}
