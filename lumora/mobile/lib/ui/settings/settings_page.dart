import 'dart:async';

import 'package:flutter/material.dart';
// `provider` also exports a `StreamProvider`; ours is the media model.
import 'package:provider/provider.dart' hide StreamProvider;

import '../../data/providers_registry.dart';
import '../../data/settings_store.dart';
import '../../models/media_item.dart';
import '../../models/stream_provider.dart';
import '../../theme/lumora_theme.dart';
import 'provider_order.dart';
import 'tmdb_key_tester.dart';

/// A preset from `lgl/view_prefs.py::ACCENT_CHOICES`.
class AccentChoice {
  const AccentChoice(this.label, this.colour);

  final String label;
  final Color colour;
}

/// The same six accents the desktop app offers, in its order.
const List<AccentChoice> accentChoices = <AccentChoice>[
  AccentChoice('Lumora blue', Color(0xFF3B82F6)),
  AccentChoice('Violet', Color(0xFF8B5CF6)),
  AccentChoice('Teal', Color(0xFF0F9D8F)),
  AccentChoice('Amber', Color(0xFFD97706)),
  AccentChoice('Rose', Color(0xFFE11D48)),
  AccentChoice('Sunflower', Color(0xFFFACC15)),
];

/// Shown wherever TMDB data appears, as on the desktop app.
const String tmdbAttribution =
    'This product uses the TMDB API but is not endorsed or certified by TMDB.';

/// The desktop release this mobile build follows.
const String appVersion = '0.6.27';

/// Settings with exactly two tabs: General and Providers.
class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Settings'),
          bottom: const TabBar(
            tabs: <Widget>[
              Tab(text: 'General'),
              Tab(text: 'Providers'),
            ],
          ),
        ),
        body: const TabBarView(
          children: <Widget>[
            _GeneralTab(),
            _ProvidersTab(),
          ],
        ),
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title, [this.description = '']);

  final String title;
  final String description;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        LumoraSpace.md,
        LumoraSpace.lg,
        LumoraSpace.md,
        LumoraSpace.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            title,
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
              color: theme.colorScheme.primary,
            ),
          ),
          if (description.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: LumoraSpace.xs),
              child: Text(
                description,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _GeneralTab extends StatelessWidget {
  const _GeneralTab();

  Future<void> _save(BuildContext context) {
    return context.read<AppSettings>().save();
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<AppSettings>();
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.only(bottom: LumoraSpace.xl),
      children: <Widget>[
        const _SectionHeader(
          'Appearance',
          'Density, accent and theme.',
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: LumoraSpace.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('Theme', style: theme.textTheme.titleSmall),
              const SizedBox(height: LumoraSpace.sm),
              SegmentedButton<ThemeMode>(
                segments: const <ButtonSegment<ThemeMode>>[
                  ButtonSegment<ThemeMode>(
                    value: ThemeMode.system,
                    label: Text('System'),
                  ),
                  ButtonSegment<ThemeMode>(
                    value: ThemeMode.dark,
                    label: Text('Dark'),
                  ),
                  ButtonSegment<ThemeMode>(
                    value: ThemeMode.light,
                    label: Text('Light'),
                  ),
                ],
                selected: <ThemeMode>{settings.themeMode},
                onSelectionChanged: (Set<ThemeMode> chosen) {
                  settings.themeMode = chosen.first;
                  unawaited(_save(context));
                },
              ),
            ],
          ),
        ),
        const SizedBox(height: LumoraSpace.lg),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: LumoraSpace.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('Accent colour', style: theme.textTheme.titleSmall),
              const SizedBox(height: LumoraSpace.sm),
              Text(
                'Used for highlights, links and selection. Labels switch to '
                'dark text when white would not read.',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: LumoraSpace.sm),
              Wrap(
                spacing: LumoraSpace.sm,
                runSpacing: LumoraSpace.sm,
                children: <Widget>[
                  for (final choice in accentChoices)
                    _AccentSwatch(
                      choice: choice,
                      selected: settings.accent.toARGB32() ==
                          choice.colour.toARGB32(),
                      onTap: () {
                        settings.accent = choice.colour;
                        unawaited(_save(context));
                      },
                    ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: LumoraSpace.sm),
        SwitchListTile(
          value: settings.compactCards,
          onChanged: (bool value) {
            settings.compactCards = value;
            unawaited(_save(context));
          },
          title: const Text('Compact layout'),
          subtitle:
              const Text('Denser rows and smaller covers, for long lists.'),
        ),
        const _SectionHeader('About', tmdbAttribution),
        ListTile(
          title: const Text('This version'),
          trailing: Text(appVersion, style: theme.textTheme.bodyMedium),
        ),
        const ListTile(
          title: Text('Movies & TV for phones'),
          subtitle: Text(
            'The media part of Lumora, with only the providers you switch on.',
          ),
        ),
      ],
    );
  }
}

class _AccentSwatch extends StatelessWidget {
  const _AccentSwatch({
    required this.choice,
    required this.selected,
    required this.onTap,
  });

  final AccentChoice choice;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final border = Theme.of(context).colorScheme.onSurface;
    return Tooltip(
      message: choice.label,
      child: InkResponse(
        onTap: onTap,
        radius: 28,
        child: Semantics(
          label: choice.label,
          selected: selected,
          button: true,
          child: Container(
            width: 48,
            height: 48,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: choice.colour,
              shape: BoxShape.circle,
              border: Border.all(
                color: selected ? border : Colors.transparent,
                width: 2,
              ),
            ),
            child: selected
                ? Icon(
                    Icons.check,
                    size: 20,
                    color: choice.colour.computeLuminance() > 0.5
                        ? const Color(0xFF111111)
                        : Colors.white,
                  )
                : null,
          ),
        ),
      ),
    );
  }
}

class _ProvidersTab extends StatefulWidget {
  const _ProvidersTab();

  @override
  State<_ProvidersTab> createState() => _ProvidersTabState();
}

class _ProvidersTabState extends State<_ProvidersTab> {
  late final TextEditingController _keyField;
  String _status = '';
  bool _testing = false;
  bool _statusIsError = false;

  @override
  void initState() {
    super.initState();
    _keyField = TextEditingController(
      text: context.read<AppSettings>().tmdbApiKey,
    );
  }

  @override
  void dispose() {
    _keyField.dispose();
    super.dispose();
  }

  Future<void> _saveKey(String value) async {
    final settings = context.read<AppSettings>();
    settings.tmdbApiKey = value.trim();
    await settings.save();
  }

  Future<void> _runTest() async {
    final settings = context.read<AppSettings>();
    final key = _keyField.text.trim();
    setState(() {
      _testing = true;
      _status = 'Testing the TMDB API…';
      _statusIsError = false;
    });
    final result = await testTmdbKey(key);
    if (!mounted) {
      return;
    }
    setState(() {
      _testing = false;
      _status = result.message;
      _statusIsError = !result.ok;
    });
    if (result.ok) {
      settings.tmdbApiKey = key;
      await settings.save();
    }
  }

  /// Every provider: the enabled ones in the user's order, then the rest.
  List<StreamProvider> _ordered(AppSettings settings) {
    final enabled = <StreamProvider>[];
    for (final id in settings.enabledProviderIds) {
      final provider = providerById(id);
      if (provider != null) {
        enabled.add(provider);
      }
    }
    final seen = enabled.map((StreamProvider p) => p.id).toSet();
    return <StreamProvider>[
      ...enabled,
      ...allProviders.where((StreamProvider p) => !seen.contains(p.id)),
    ];
  }

  void _reorder(AppSettings settings, int oldIndex, int newIndex) {
    final order = _ordered(settings).map((StreamProvider p) => p.id).toList();
    final enabled = settings.enabledProviderIds.toSet();
    settings.setEnabledProviderIds(
      reorderEnabled(order, enabled, oldIndex, newIndex),
    );
    unawaited(settings.save());
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<AppSettings>();
    final theme = Theme.of(context);
    final providers = _ordered(settings);
    return ListView(
      padding: const EdgeInsets.only(bottom: LumoraSpace.xl),
      children: <Widget>[
        const _SectionHeader(
          'TMDB',
          'TMDB supplies movie and TV show titles, release dates, genres, '
              'descriptions, ratings, posters, and backdrops. It does not provide '
              'video files; playback is handled separately. Requires a free API '
              'key and TMDB attribution.',
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: LumoraSpace.md),
          child: TextField(
            controller: _keyField,
            obscureText: true,
            autocorrect: false,
            enableSuggestions: false,
            decoration: const InputDecoration(
              labelText: 'TMDB API key',
              border: OutlineInputBorder(),
              helperMaxLines: 3,
            ),
            onSubmitted: (String value) => unawaited(_saveKey(value)),
            onChanged: (String value) {
              if (_status.isNotEmpty) {
                setState(() {
                  _status = '';
                  _statusIsError = false;
                });
              }
            },
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(
            LumoraSpace.md,
            LumoraSpace.sm,
            LumoraSpace.md,
            0,
          ),
          child: Row(
            children: <Widget>[
              FilledButton(
                onPressed: _testing ? null : () => unawaited(_runTest()),
                child: Text(_testing ? 'Testing…' : 'Test key'),
              ),
              const SizedBox(width: LumoraSpace.sm),
              TextButton(
                onPressed: _testing
                    ? null
                    : () {
                        _keyField.clear();
                        unawaited(_saveKey(''));
                        setState(() {
                          _status = '';
                          _statusIsError = false;
                        });
                      },
                child: const Text('Clear'),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(
            LumoraSpace.md,
            LumoraSpace.xs,
            LumoraSpace.md,
            0,
          ),
          child: SelectableText(
            'A free key is enough: register at themoviedb.org/settings/api '
            'and copy the API key.',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        ),
        if (_status.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(
              LumoraSpace.md,
              LumoraSpace.sm,
              LumoraSpace.md,
              0,
            ),
            child: Text(
              _status,
              style: theme.textTheme.bodySmall?.copyWith(
                color: _statusIsError
                    ? theme.colorScheme.error
                    : theme.colorScheme.primary,
              ),
            ),
          ),
        const _SectionHeader(
          'Playback sources',
          'Streaming providers, in the order the player tries them. Drag to '
              'reorder; pick one as the default.',
        ),
        ReorderableListView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          buildDefaultDragHandles: false,
          itemCount: providers.length,
          onReorder: (int oldIndex, int newIndex) =>
              _reorder(settings, oldIndex, newIndex),
          itemBuilder: (BuildContext context, int index) {
            final provider = providers[index];
            final enabled = settings.isProviderEnabled(provider.id);
            return _ProviderTile(
              key: ValueKey<String>(provider.id),
              provider: provider,
              index: index,
              enabled: enabled,
              isDefault: settings.defaultProviderId == provider.id,
              onToggled: (bool value) {
                settings.setProviderEnabled(provider.id, value);
                unawaited(settings.save());
              },
              onMakeDefault: () {
                settings.defaultProviderId = provider.id;
                unawaited(settings.save());
              },
            );
          },
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(
            LumoraSpace.md,
            LumoraSpace.md,
            LumoraSpace.md,
            0,
          ),
          child: Text(
            'Third-party services: availability, regions and formats are '
            'outside our control.',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        ),
      ],
    );
  }
}

class _ProviderTile extends StatelessWidget {
  const _ProviderTile({
    required this.provider,
    required this.index,
    required this.enabled,
    required this.isDefault,
    required this.onToggled,
    required this.onMakeDefault,
    super.key,
  });

  final StreamProvider provider;
  final int index;
  final bool enabled;
  final bool isDefault;
  final ValueChanged<bool> onToggled;
  final VoidCallback onMakeDefault;

  String get _kinds {
    const labels = <MediaKind, String>{
      MediaKind.movie: 'films',
      MediaKind.tv: 'series',
      MediaKind.anime: 'anime',
    };
    final names = provider.kinds.map((MediaKind kind) => labels[kind]!);
    return 'Plays ${names.join(', ')}.';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Material(
      color: Colors.transparent,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: LumoraSpace.sm),
        child: Row(
          children: <Widget>[
            ReorderableDragStartListener(
              index: index,
              child: const Padding(
                padding: EdgeInsets.all(LumoraSpace.sm),
                child: Icon(Icons.drag_handle),
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    provider.name,
                    style: theme.textTheme.bodyLarge?.copyWith(
                      color: enabled
                          ? theme.colorScheme.onSurface
                          : theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                  Text(
                    _kinds,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            // A radio-styled button, not a `Radio`: the tile is rebuilt inside
            // the drag proxy, where an inherited `RadioGroup` is not above it.
            IconButton(
              onPressed: enabled && !isDefault ? onMakeDefault : null,
              tooltip: 'Default provider',
              icon: Icon(
                isDefault
                    ? Icons.radio_button_checked
                    : Icons.radio_button_unchecked,
              ),
            ),
            Switch(value: enabled, onChanged: onToggled),
          ],
        ),
      ),
    );
  }
}
