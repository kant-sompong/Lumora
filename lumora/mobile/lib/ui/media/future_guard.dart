import 'dart:async';

/// Keeps a repository future from being reported as an unhandled error.
///
/// A FutureBuilder subscribes on the next build; a fast failure (a cached
/// error, a missing key) can complete the future first, and Dart then treats
/// the error as uncaught. This attaches a listener immediately without
/// swallowing anything: the original future is returned unchanged, so the
/// FutureBuilder still renders its error state.
Future<T> guarded<T>(Future<T> future) {
  unawaited(future.then<void>((_) {}, onError: (Object _, StackTrace _) {}));
  return future;
}
