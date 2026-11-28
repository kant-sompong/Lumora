import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';

import '../../data/provider_health.dart';
import '../../models/media_item.dart';
import '../../theme/lumora_theme.dart';
import '../media/media_detail_page.dart';
import 'embed_frame.dart';
import 'player_panel.dart';
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
/// The page is deliberately bare: landscape, immersive, black, and with no
/// chrome of its own. Providers draw their own controls in the corners, so the
/// only thing this app adds is a small handle on the LEFT edge that slides a
/// panel in when tapped. Nothing is ever drawn over the video otherwise.
class PlayerPage extends StatefulWidget {
  const PlayerPage({
    required this.url,
    required this.title,
    this.item,
    this.targets = const <PlaybackTarget>[],
    this.initialIndex = 0,
    this.season,
    this.episode,
    this.health,
    this.autoplay = true,
    this.webViewForTest,
    super.key,
  });

  final Uri url;
  final String title;

  /// The catalogue entry being played, so the panel can open its own page.
  /// Null when the caller has no entry (tests, a bare URL): the panel's back
  /// arrow then leaves the player as before.
  final MediaItem? item;

  /// Every provider that can play the title, best first.
  final List<PlaybackTarget> targets;

  /// Which entry of [targets] [url] came from.
  final int initialIndex;

  /// Shown in the panel as `Season 1 · Episode 3`; null for a film.
  final int? season;
  final int? episode;

  /// Cached health, so the panel can show a dot and a time per provider.
  final ProviderHealthService? health;

  /// Whether the provider's page may start the video by itself. Off holds
  /// playback until the viewer presses play in the provider's own player
  /// (Settings → Autoplay).
  final bool autoplay;

  /// Replaces the WebView in widget tests, which have no platform view.
  @visibleForTesting
  final Widget? webViewForTest;

  /// The left-edge handle tab; tests tap it to open the panel.
  static const Key handleKey = Key('playerHandle');

  /// The thin left-edge strip, which opens the panel too.
  static const Key edgeStripKey = Key('playerEdgeStrip');

  @override
  State<PlayerPage> createState() => _PlayerPageState();
}

class _PlayerPageState extends State<PlayerPage> {
  /// How long the handle stays brighter after the panel closes.
  static const Duration _handleLinger = Duration(seconds: 3);

  /// Width of the panel and of the handle tab.
  static const double _panelWidth = 280;
  static const double _handleWidth = 28;
  static const double _handleHeight = 96;

  /// The thin strip that also opens the panel.
  static const double _edgeStripWidth = 24;

  WebViewController? _controller;
  late final Widget _webView;
  late int _index;
  late Uri _url;
  bool _loading = true;
  String? _error;
  bool _panelOpen = false;
  bool _handleFaded = false;
  Timer? _handleTimer;
  Route<void>? _fullscreenRoute;

  String? get _subtitle {
    final episode = widget.episode;
    if (episode == null) {
      return null;
    }
    final season = widget.season;
    return season == null
        ? 'Episode $episode'
        : 'Season $season · Episode $episode';
  }

  @override
  void initState() {
    super.initState();
    _index = widget.initialIndex.clamp(
      0,
      widget.targets.isEmpty ? 0 : widget.targets.length - 1,
    );
    _url = widget.url;
    // Landscape and immersive before the first frame, so the provider's page
    // never lays out in portrait and then rotates.
    unawaited(_enterPlayerChrome());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        unawaited(_enterPlayerChrome());
      }
    });

    final controller = widget.webViewForTest == null ? WebViewController() : null;
    _controller = controller;
    // Without a WebView (widget tests) there is nothing to load, so no spinner.
    _loading = controller != null;
    _webView =
        widget.webViewForTest ??
        WebViewWidget(controller: controller!);
    if (controller != null) {
      unawaited(_configure(controller));
    }
    _restartHandleTimer();
  }

  @override
  void dispose() {
    _handleTimer?.cancel();
    // Back to the rest of the app: portrait, normal system UI.
    unawaited(
      SystemChrome.setPreferredOrientations(<DeviceOrientation>[
        DeviceOrientation.portraitUp,
      ]),
    );
    unawaited(SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge));
    super.dispose();
  }

  Future<void> _enterPlayerChrome() async {
    await SystemChrome.setPreferredOrientations(<DeviceOrientation>[
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
    await SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
  }

  Future<void> _configure(WebViewController controller) async {
    await controller.setJavaScriptMode(JavaScriptMode.unrestricted);
    await controller.setBackgroundColor(Colors.black);
    await controller.setNavigationDelegate(
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
    final platform = controller.platform;
    if (platform is AndroidWebViewController) {
      // Autoplay starts the video without a tap; with it off the page may only
      // start media after the viewer touches play. Inline playback and smooth
      // scrolling stay on, and Android's hardware acceleration is switched on
      // in the manifest (`android:hardwareAccelerated="true"`);
      // webview_flutter exposes no per-view switch for it.
      await platform.setMediaPlaybackRequiresUserGesture(!widget.autoplay);
      await platform.setOverScrollMode(WebViewOverScrollMode.never);
      await platform.setCustomWidgetCallbacks(
        onShowCustomWidget: _showFullscreenVideo,
        onHideCustomWidget: _hideFullscreenVideo,
      );
    }
    await _loadUrl(controller, _url);
  }

  /// The provider on screen, when the caller listed its providers.
  String? get _providerId {
    if (_index < 0 || _index >= widget.targets.length) {
      return null;
    }
    return widget.targets[_index].providerId;
  }

  /// Loads [url] the way its provider expects.
  ///
  /// anipm.pk redirects an embed opened as a page, so its embed goes inside a
  /// small framing document; every other provider is loaded directly.
  Future<void> _loadUrl(WebViewController controller, Uri url) {
    if (needsEmbedFrame(_providerId ?? '')) {
      return controller.loadHtmlString(
        buildEmbedFrameHtml(url),
        baseUrl: embedFrameBaseUrl,
      );
    }
    return controller.loadRequest(url);
  }

  NavigationDecision _onNavigationRequest(NavigationRequest request) {
    // The framing document itself is not the provider's page, so its own load
    // is allowed before the host rule applies to anything else.
    if (needsEmbedFrame(_providerId ?? '') &&
        request.isMainFrame &&
        isEmbedFrameDocumentUrl(request.url)) {
      return NavigationDecision.navigate;
    }
    // Only the page itself is held to the provider's host; a sub-frame may load
    // a video from a CDN or another site, or the player would stay empty.
    if (shouldNavigateRequest(
      request.url,
      _url,
      isMainFrame: request.isMainFrame,
    )) {
      return NavigationDecision.navigate;
    }
    final requested = Uri.tryParse(request.url) ?? Uri();
    // Popups and off-site links are refused, never opened in a new window:
    // this is a player, not a browser.
    debugPrint(
      'Player blocked navigation to ${requested.toString().split('?').first} '
      '(${blockedNavigationReason(requested, _url)}).',
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

  void _restartHandleTimer() {
    _handleTimer?.cancel();
    _handleTimer = Timer(_handleLinger, () {
      if (mounted && !_panelOpen) {
        setState(() => _handleFaded = true);
      }
    });
  }

  void _openPanel() {
    _handleTimer?.cancel();
    if (!_panelOpen || _handleFaded) {
      setState(() {
        _panelOpen = true;
        _handleFaded = false;
      });
    }
  }

  void _closePanel() {
    if (!_panelOpen) {
      return;
    }
    setState(() => _panelOpen = false);
    _restartHandleTimer();
  }

  void _retry() {
    final controller = _controller;
    setState(() {
      _error = null;
      _loading = controller != null;
    });
    if (controller != null) {
      unawaited(_loadUrl(controller, _url));
    }
  }

  void _load(int index) {
    if (index < 0 || index >= widget.targets.length) {
      return;
    }
    final controller = _controller;
    setState(() {
      _index = index;
      _url = widget.targets[index].url;
      _error = null;
      _loading = controller != null;
    });
    if (controller != null) {
      unawaited(_loadUrl(controller, _url));
    }
  }

  void _nextProvider() {
    if (widget.targets.length < 2) {
      return;
    }
    _load((_index + 1) % widget.targets.length);
    _closePanel();
  }

  void _selectProvider(int index) {
    _load(index);
    _closePanel();
  }

  /// The panel's back arrow: the playing title's own page, or the way back
  /// when the player was opened without a catalogue entry.
  ///
  /// `Navigator.maybePop()` cannot be used here: with the panel open the page's
  /// [PopScope] refuses the pop and closes the panel instead, which is exactly
  /// what the X already does. `pop()` is unconditional, so the fallback leaves
  /// the player even while the panel is open.
  void _openDetails() {
    final item = widget.item;
    final navigator = Navigator.of(context);
    if (item == null) {
      navigator.pop();
      return;
    }
    navigator.pushReplacement(
      MaterialPageRoute<void>(builder: (_) => MediaDetailPage(item: item)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      // The system back gesture closes the panel first, then the player.
      canPop: !_panelOpen,
      onPopInvokedWithResult: (bool didPop, Object? result) {
        if (!didPop && _panelOpen) {
          _closePanel();
        }
      },
      child: Scaffold(
        backgroundColor: Colors.black,
        body: Stack(
          fit: StackFit.expand,
          children: <Widget>[
            // The one WebView, kept in the tree: opening the panel never
            // rebuilds or reloads it.
            _webView,
            // A black cover until the provider paints, so no white flash.
            if (_loading && _error == null)
              const ColoredBox(color: Colors.black),
            if (_loading && _error == null)
              const Center(
                child: CircularProgressIndicator(color: Colors.white),
              ),
            if (_error != null) _buildError(context),
            if (!_panelOpen) _buildEdgeStrip(),
            if (!_panelOpen) _buildHandle(),
            if (_panelOpen) _buildScrim(),
            _buildPanel(),
          ],
        ),
      ),
    );
  }

  Widget _buildEdgeStrip() {
    return Positioned(
      left: 0,
      top: 0,
      bottom: 0,
      width: _edgeStripWidth,
      child: Semantics(
        button: true,
        label: 'Player menu',
        child: GestureDetector(
          key: PlayerPage.edgeStripKey,
          behavior: HitTestBehavior.opaque,
          onTap: _openPanel,
          child: const SizedBox.expand(),
        ),
      ),
    );
  }

  Widget _buildHandle() {
    return Positioned(
      left: 0,
      top: 0,
      bottom: 0,
      child: Center(
        child: AnimatedOpacity(
          opacity: _handleFaded ? 0.28 : 0.6,
          duration: const Duration(milliseconds: 250),
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: _openPanel,
            child: Semantics(
              button: true,
              label: 'Player menu',
              child: Container(
                key: PlayerPage.handleKey,
                width: _handleWidth,
                height: _handleHeight,
                decoration: const BoxDecoration(
                  color: Color(0x99000000),
                  borderRadius: BorderRadius.horizontal(
                    right: Radius.circular(14),
                  ),
                ),
                child: const Icon(
                  Icons.chevron_right,
                  color: Color(0xCCFFFFFF),
                  size: 20,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildScrim() {
    return Positioned.fill(
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: _closePanel,
        child: const ColoredBox(color: Color(0x66000000)),
      ),
    );
  }

  Widget _buildPanel() {
    return AnimatedPositioned(
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      left: _panelOpen ? 0 : -_panelWidth,
      top: 0,
      bottom: 0,
      width: _panelWidth,
      child: IgnorePointer(
        ignoring: !_panelOpen,
        child: PlayerPanel(
          title: widget.title,
          subtitle: _subtitle,
          targets: widget.targets,
          selectedIndex: _index,
          health: widget.health,
          onBack: _openDetails,
          onClose: _closePanel,
          onNextProvider: _nextProvider,
          onSelectProvider: _selectProvider,
          onReload: _retry,
        ),
      ),
    );
  }

  Widget _buildError(BuildContext context) {
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
            style: const TextStyle(
              color: Colors.white,
              fontSize: 16,
              fontWeight: FontWeight.w600,
            ),
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
          if (widget.targets.length > 1)
            TextButton(
              onPressed: _nextProvider,
              child: const Text('Next provider'),
            ),
        ],
      ),
    );
  }
}
