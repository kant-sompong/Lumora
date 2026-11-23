import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';

import '../../theme/lumora_theme.dart';
import 'player_policy.dart';

/// One provider's ready-to-load playback URL.
class PlaybackTarget {
  const PlaybackTarget({
    required this.providerId,
    required this.providerName,
    required this.url,
  });

  final String providerId;
  final String providerName;
  final Uri url;
}

/// One full-screen WebView playing a provider's embed.
///
/// There is no browser chrome here on purpose: no address bar, no tabs and no
/// history list. The overlay carries a back button, the title and a way to
/// switch to the next enabled provider.
class PlayerPage extends StatefulWidget {
  const PlayerPage({
    required this.url,
    required this.title,
    this.targets = const <PlaybackTarget>[],
    this.initialIndex = 0,
    super.key,
  });

  final Uri url;
  final String title;

  /// Every enabled provider that can play the title, in the user's order.
  final List<PlaybackTarget> targets;

  /// Which entry of [targets] [url] came from.
  final int initialIndex;

  @override
  State<PlayerPage> createState() => _PlayerPageState();
}

class _PlayerPageState extends State<PlayerPage> {
  static const Duration _overlayLinger = Duration(seconds: 4);

  late final WebViewController _controller;
  late int _index;
  late Uri _url;
  Timer? _hideTimer;
  bool _overlayVisible = true;
  bool _loading = true;
  String? _error;
  Route<void>? _fullscreenRoute;

  Uri get _origin => _url;

  PlaybackTarget? get _target => _index >= 0 && _index < widget.targets.length
      ? widget.targets[_index]
      : null;

  @override
  void initState() {
    super.initState();
    _index = widget.initialIndex.clamp(
      0,
      widget.targets.isEmpty ? 0 : widget.targets.length - 1,
    );
    _url = widget.url;
    _enterFullscreenChrome();
    _controller = WebViewController();
    unawaited(_configure());
    _restartHideTimer();
  }

  @override
  void dispose() {
    _hideTimer?.cancel();
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    SystemChrome.setPreferredOrientations(<DeviceOrientation>[
      DeviceOrientation.portraitUp,
    ]);
    super.dispose();
  }

  Future<void> _enterFullscreenChrome() async {
    await SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    await SystemChrome.setPreferredOrientations(<DeviceOrientation>[
      DeviceOrientation.portraitUp,
      DeviceOrientation.portraitDown,
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
  }

  Future<void> _configure() async {
    await _controller.setJavaScriptMode(JavaScriptMode.unrestricted);
    await _controller.setBackgroundColor(Colors.black);
    await _controller.setNavigationDelegate(
      NavigationDelegate(
        onNavigationRequest: _onNavigationRequest,
        onPageStarted: (String url) {
          if (!mounted) {
            return;
          }
          setState(() {
            _loading = true;
            _error = null;
          });
        },
        onPageFinished: (String url) {
          if (!mounted) {
            return;
          }
          setState(() => _loading = false);
        },
        onWebResourceError: (WebResourceError error) {
          if (!mounted || error.isForMainFrame == false) {
            return;
          }
          setState(() {
            _loading = false;
            _error = error.description.isEmpty
                ? 'The provider did not load.'
                : error.description;
          });
        },
      ),
    );
    final platform = _controller.platform;
    if (platform is AndroidWebViewController) {
      // Players start unmuted and may go fullscreen in the same window.
      await platform.setMediaPlaybackRequiresUserGesture(false);
      await platform.setCustomWidgetCallbacks(
        onShowCustomWidget: _showFullscreenVideo,
        onHideCustomWidget: _hideFullscreenVideo,
      );
    }
    await _controller.loadRequest(_url);
  }

  NavigationDecision _onNavigationRequest(NavigationRequest request) {
    if (shouldNavigate(request.url, _origin)) {
      return NavigationDecision.navigate;
    }
    final requested = Uri.tryParse(request.url) ?? Uri();
    // Popups and off-site links are refused, never opened in a new window:
    // this is a player, not a browser.
    debugPrint(
      'Player blocked navigation to ${requested.toString().split('?').first} '
      '(${blockedNavigationReason(requested, _origin)}).',
    );
    return NavigationDecision.prevent;
  }

  void _showFullscreenVideo(Widget widget, VoidCallback onHidden) {
    if (!mounted) {
      onHidden();
      return;
    }
    final route = PageRouteBuilder<void>(
      opaque: true,
      pageBuilder: (BuildContext context, _, __) => Scaffold(
        backgroundColor: Colors.black,
        body: widget,
      ),
    );
    _fullscreenRoute = route;
    unawaited(
      Navigator.of(context).push(route).then((_) => _fullscreenRoute = null),
    );
  }

  void _hideFullscreenVideo() {
    final route = _fullscreenRoute;
    if (route == null) {
      return;
    }
    _fullscreenRoute = null;
    final navigator = Navigator.of(context);
    if (route.isCurrent) {
      navigator.pop();
    }
  }

  void _restartHideTimer() {
    _hideTimer?.cancel();
    _hideTimer = Timer(_overlayLinger, () {
      if (mounted) {
        setState(() => _overlayVisible = false);
      }
    });
  }

  void _showOverlay() {
    if (!_overlayVisible) {
      setState(() => _overlayVisible = true);
    }
    _restartHideTimer();
  }

  void _retry() {
    setState(() {
      _error = null;
      _loading = true;
    });
    unawaited(_controller.loadRequest(_url));
  }

  void _switchProvider() {
    if (widget.targets.length < 2) {
      return;
    }
    setState(() {
      _index = (_index + 1) % widget.targets.length;
      _url = widget.targets[_index].url;
      _error = null;
      _loading = true;
    });
    _restartHideTimer();
    unawaited(_controller.loadRequest(_url));
  }

  @override
  Widget build(BuildContext context) {
    final target = _target;
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: <Widget>[
          WebViewWidget(controller: _controller),
          if (_loading && _error == null)
            const Center(
              child: CircularProgressIndicator(color: Colors.white),
            ),
          if (_error != null) _buildError(context),
          // A thin strip at the top brings the overlay back without stealing
          // taps from the video itself.
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            height: LumoraSpace.xxl + LumoraSpace.sm,
            child: GestureDetector(
              behavior: HitTestBehavior.translucent,
              onTap: _showOverlay,
            ),
          ),
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: AnimatedOpacity(
              opacity: _overlayVisible ? 1 : 0,
              duration: const Duration(milliseconds: 180),
              child: IgnorePointer(
                ignoring: !_overlayVisible,
                child: _buildOverlay(context, target),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildOverlay(BuildContext context, PlaybackTarget? target) {
    final canSwitch = widget.targets.length > 1;
    return SafeArea(
      bottom: false,
      child: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: <Color>[Color(0xCC000000), Color(0x00000000)],
          ),
        ),
        padding: const EdgeInsets.symmetric(
          horizontal: LumoraSpace.sm,
          vertical: LumoraSpace.xs,
        ),
        child: Row(
          children: <Widget>[
            IconButton(
              onPressed: () => Navigator.of(context).maybePop(),
              tooltip: 'Back',
              icon: const Icon(Icons.arrow_back, color: Colors.white),
            ),
            const SizedBox(width: LumoraSpace.xs),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    widget.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  if (target != null)
                    Text(
                      target.providerName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: Color(0xB3FFFFFF),
                        fontSize: 11,
                      ),
                    ),
                ],
              ),
            ),
            if (canSwitch)
              IconButton(
                onPressed: _switchProvider,
                tooltip: 'Switch provider',
                icon: const Icon(Icons.swap_horiz, color: Colors.white),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildError(BuildContext context) {
    final theme = LumoraTheme.dark(const Color(0xFF8557EA));
    return Container(
      color: Colors.black,
      alignment: Alignment.center,
      padding: const EdgeInsets.all(LumoraSpace.lg),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const Icon(Icons.error_outline, color: Colors.white70, size: 40),
          const SizedBox(height: LumoraSpace.md),
          Text(
            widget.title,
            textAlign: TextAlign.center,
            style: theme.textTheme.titleMedium?.copyWith(color: Colors.white),
          ),
          const SizedBox(height: LumoraSpace.sm),
          Text(
            _error ?? '',
            textAlign: TextAlign.center,
            style: const TextStyle(color: Color(0xB3FFFFFF), fontSize: 13),
          ),
          const SizedBox(height: LumoraSpace.lg),
          FilledButton.icon(
            onPressed: _retry,
            icon: const Icon(Icons.refresh),
            label: const Text('Retry'),
          ),
          if (widget.targets.length > 1) ...<Widget>[
            const SizedBox(height: LumoraSpace.sm),
            TextButton(
              onPressed: _switchProvider,
              child: const Text('Switch provider'),
            ),
          ],
        ],
      ),
    );
  }
}
