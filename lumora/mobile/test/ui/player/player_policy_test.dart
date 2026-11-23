import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/ui/player/player_policy.dart';

void main() {
  final origin = Uri.parse('https://cinesrc.st/embed/movie/603');

  group('isAllowedNavigation', () {
    test('lets the provider\'s own pages through', () {
      expect(
        isAllowedNavigation(
          Uri.parse('https://cinesrc.st/embed/tv/1399?s=1&e=2'),
          origin,
        ),
        isTrue,
      );
    });

    test('allows the www form of the provider host', () {
      expect(
        isAllowedNavigation(
          Uri.parse('https://www.rivestream.app/embed?type=movie&id=1'),
          Uri.parse('https://rivestream.app/embed?type=movie&id=1'),
        ),
        isTrue,
      );
    });

    test('allows the provider\'s own subdomains for its assets', () {
      expect(
        isAllowedNavigation(
            Uri.parse('https://cdn.cinesrc.st/player.js'), origin),
        isTrue,
      );
    });

    test('blocks another site', () {
      expect(
        isAllowedNavigation(Uri.parse('https://ads.example.com/pop'), origin),
        isFalse,
      );
    });

    test('blocks a lookalike host', () {
      expect(
        isAllowedNavigation(Uri.parse('https://cinesrc.st.evil.com/x'), origin),
        isFalse,
      );
      expect(
        isAllowedNavigation(Uri.parse('https://notcinesrc.st/x'), origin),
        isFalse,
      );
    });

    test('blocks app and file schemes a provider tries to launch', () {
      for (final url in <String>[
        'intent://scan/#Intent;scheme=zxing;end',
        'market://details?id=com.example',
        'file:///sdcard/Download/movie.mp4',
        'content://media/external/video/1',
        'mailto:someone@example.com',
      ]) {
        expect(isAllowedNavigation(Uri.parse(url), origin), isFalse,
            reason: url);
      }
    });

    test('lets inline document schemes through', () {
      for (final url in <String>[
        'about:blank',
        'data:text/html,<p>x',
        'blob:https://cinesrc.st/abc'
      ]) {
        expect(isAllowedNavigation(Uri.parse(url), origin), isTrue,
            reason: url);
      }
    });

    test('allows a same-host upgrade from http to https', () {
      expect(
        isAllowedNavigation(Uri.parse('http://cinesrc.st/embed/x'), origin),
        isTrue,
      );
    });

    test('refuses a request with no host at all', () {
      expect(isAllowedNavigation(Uri.parse('https:///embed'), origin), isFalse);
    });
  });

  group('shouldNavigate (the delegate decision)', () {
    test('navigates on the provider host', () {
      expect(
          shouldNavigate('https://cinesrc.st/embed/movie/603', origin), isTrue);
    });

    test('prevents a cross-host request', () {
      expect(shouldNavigate('https://tracker.example/t?v=1', origin), isFalse);
    });

    test('prevents an unparseable request', () {
      expect(shouldNavigate('ht tp://bad url', origin), isFalse);
    });
  });

  group('shouldNavigateRequest (main frame vs sub-frames)', () {
    test('a cross-host sub-frame is allowed to load', () {
      expect(
        shouldNavigateRequest(
          'https://cdn.videohost.example/player.html?src=1',
          origin,
          isMainFrame: false,
        ),
        isTrue,
      );
    });

    test('a sub-frame with a non-web scheme is allowed to load', () {
      expect(
        shouldNavigateRequest(
          'blob:https://cinesrc.st/abc',
          origin,
          isMainFrame: false,
        ),
        isTrue,
      );
    });

    test('the main frame is still held to the provider host', () {
      expect(
        shouldNavigateRequest(
          'https://ads.example.com/pop',
          origin,
          isMainFrame: true,
        ),
        isFalse,
      );
      expect(
        shouldNavigateRequest(
          'https://cinesrc.st/embed/tv/1399?s=2&e=1',
          origin,
          isMainFrame: true,
        ),
        isTrue,
      );
    });
  });

  test('blockedNavigationReason never leaks a query string', () {
    final reason = blockedNavigationReason(
      Uri.parse('https://evil.example/x?key=secret'),
      origin,
    );
    expect(reason, contains('evil.example'));
    expect(reason, isNot(contains('secret')));
  });
}
