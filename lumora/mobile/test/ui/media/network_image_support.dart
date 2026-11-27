import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

/// A 1x1 transparent PNG, enough for `Image.network` to decode in a widget
/// test without a network.
final Uint8List _pngBytes = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA'
  'hKmMIQAAAABJRU5ErkJggg==',
);

/// Replaces Flutter's default failing test HTTP client with one that serves
/// [_pngBytes] to every URL, throws for a URL whose path contains `broken`,
/// and answers a URL containing `slow` after a delay.
///
/// Returns the previous overrides so [restoreImageHttp] can put them back.
HttpOverrides? installFakeImageHttp() {
  final previous = HttpOverrides.current;
  HttpOverrides.global = _ImageHttpOverrides();
  return previous;
}

void restoreImageHttp(HttpOverrides? previous) {
  HttpOverrides.global = previous;
}

class _ImageHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) => _FakeHttpClient();
}

class _FakeHttpClient implements HttpClient {
  @override
  bool autoUncompress = true;

  @override
  Future<HttpClientRequest> getUrl(Uri url) async => _FakeRequest(url);

  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async =>
      _FakeRequest(url);

  @override
  void close({bool force = false}) {}

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeRequest implements HttpClientRequest {
  _FakeRequest(this.uri);

  @override
  final Uri uri;

  @override
  final HttpHeaders headers = _FakeHeaders();

  @override
  Future<HttpClientResponse> close() {
    final path = uri.path;
    if (path.contains('broken')) {
      return Future<HttpClientResponse>.error(
        const SocketException('no image at this address'),
      );
    }
    if (path.contains('slow')) {
      return Future<HttpClientResponse>.delayed(
        const Duration(milliseconds: 300),
        _FakeResponse.new,
      );
    }
    return Future<HttpClientResponse>.value(_FakeResponse());
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeHeaders implements HttpHeaders {
  @override
  void add(String name, Object value, {bool preserveHeaderCase = false}) {}

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeResponse implements HttpClientResponse {
  @override
  int get statusCode => HttpStatus.ok;

  @override
  int get contentLength => _pngBytes.length;

  @override
  HttpClientResponseCompressionState get compressionState =>
      HttpClientResponseCompressionState.notCompressed;

  @override
  StreamSubscription<List<int>> listen(
    void Function(List<int> event)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) {
    return Stream<List<int>>.value(_pngBytes).listen(
      onData,
      onError: onError,
      onDone: onDone,
      cancelOnError: cancelOnError,
    );
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
