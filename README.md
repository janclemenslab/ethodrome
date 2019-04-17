# Ethodrome: master & service

## deploy
- `pip install git+http://github.com/janclemenslab/ethodrome.git`
- [Node setup](https://github.com/janclemenslab/ethodrome/wiki/Node-setup)
- [Head setup](https://github.com/janclemenslab/ethodrome/wiki/Head-setup)

## Documentation
- [Services](https://github.com/janclemenslab/ethodrome/wiki/Services)
- [Playlists](https://github.com/janclemenslab/ethodrome/wiki/Playlists)
- [Configuration files](https://github.com/janclemenslab/ethoconfig)
- Calibration of [sound](https://github.com/janclemenslab/ethodrome/wiki/Calibrating-sound-intensity) and [optogenetics](https://github.com/janclemenslab/ethodrome/wiki/Calibrating-LED-light-intensity-for-optogenetics).

## TODO
- [ ] present logging information within gui
- [ ] make gui more responsive through async
- [ ] make protocols editable so small changes are more easily done (use traisui)
- [ ] refactor? ethoservice->services, ethomaster->master?
- [x] switch over to yaml for config reading (since this will preserve types)
- [x] installation instructions (where to copy `.ethoconfig.ini`)
- [x] copy ethoservice wiki
- [x] switch over to new zerorpc and pickle on localhost tasks
