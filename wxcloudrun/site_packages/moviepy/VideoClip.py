"""
This module implements VideoClip (base class for video clips) and its
main subclasses:
- Animated clips:     VideofileClip, ImageSequenceClip
- Static image clips: ImageClip, ColorClip, TextClip,
"""
import os
import proglog

from Clip import Clip
from decorators import (convert_masks_to_RGB, requires_duration, use_clip_fps_by_default)
from tools import (extensions_dict, find_extension,is_string)
from ffmpeg_writer import ffmpeg_write_video


class VideoClip(Clip):
    """Base class for video clips.

    See ``VideoFileClip``, ``ImageClip`` etc. for more user-friendly
    classes.


    Parameters
    -----------

    ismask
      `True` if the clip is going to be used as a mask.


    Attributes
    ----------

    size
      The size of the clip, (width,heigth), in pixels.

    w, h
      The width and height of the clip, in pixels.

    ismask
      Boolean set to `True` if the clip is a mask.

    make_frame
      A function ``t-> frame at time t`` where ``frame`` is a
      w*h*3 RGB array.

    mask (default None)
      VideoClip mask attached to this clip. If mask is ``None``,
                The video clip is fully opaque.

    audio (default None)
      An AudioClip instance containing the audio of the video clip.

    pos
      A function ``t->(x,y)`` where ``x,y`` is the position
      of the clip when it is composed with other clips.
      See ``VideoClip.set_pos`` for more details

    relative_pos
      See variable ``pos``.

    """

    def __init__(self, make_frame=None, ismask=False, duration=None,
                 has_constant_size=True):
        Clip.__init__(self)
        self.mask = None
        self.audio = None
        self.pos = lambda t: (0, 0)
        self.relative_pos = False
        if make_frame:
            self.make_frame = make_frame
            self.size = self.get_frame(0).shape[:2][::-1]
        self.ismask = ismask
        self.has_constant_size=has_constant_size
        if duration is not None:
            self.duration = duration
            self.end = duration


    @requires_duration
    @use_clip_fps_by_default
    @convert_masks_to_RGB
    def write_videofile(self, filename, fps=None, codec=None,
                        bitrate=None, audio=True, audio_fps=44100,
                        preset="medium",
                        audio_nbytes=4, audio_codec=None,
                        audio_bitrate=None, audio_bufsize=2000,
                        temp_audiofile=None,
                        rewrite_audio=True, remove_temp=True,
                        write_logfile=False, verbose=True,
                        threads=None, ffmpeg_params=None,
                        logger='bar'):
        """Write the clip to a videofile.

        Parameters
        -----------

        filename
          Name of the video file to write in.
          The extension must correspond to the "codec" used (see below),
          or simply be '.avi' (which will work with any codec).

        fps
          Number of frames per second in the resulting video file. If None is
          provided, and the clip has an fps attribute, this fps will be used.

        codec
          Codec to use for image encoding. Can be any codec supported
          by ffmpeg. If the filename is has extension '.mp4', '.ogv', '.webm',
          the codec will be set accordingly, but you can still set it if you
          don't like the default. For other extensions, the output filename
          must be set accordingly.

          Some examples of codecs are:

          ``'libx264'`` (default codec for file extension ``.mp4``)
          makes well-compressed videos (quality tunable using 'bitrate').


          ``'mpeg4'`` (other codec for extension ``.mp4``) can be an alternative
          to ``'libx264'``, and produces higher quality videos by default.


          ``'rawvideo'`` (use file extension ``.avi``) will produce
          a video of perfect quality, of possibly very huge size.


          ``png`` (use file extension ``.avi``) will produce a video
          of perfect quality, of smaller size than with ``rawvideo``.


          ``'libvorbis'`` (use file extension ``.ogv``) is a nice video
          format, which is completely free/ open source. However not
          everyone has the codecs installed by default on their machine.


          ``'libvpx'`` (use file extension ``.webm``) is tiny a video
          format well indicated for web videos (with HTML5). Open source.


        audio
          Either ``True``, ``False``, or a file name.
          If ``True`` and the clip has an audio clip attached, this
          audio clip will be incorporated as a soundtrack in the movie.
          If ``audio`` is the name of an audio file, this audio file
          will be incorporated as a soundtrack in the movie.

        audiofps
          frame rate to use when generating the sound.

        temp_audiofile
          the name of the temporary audiofile to be generated and
          incorporated in the the movie, if any.

        audio_codec
          Which audio codec should be used. Examples are 'libmp3lame'
          for '.mp3', 'libvorbis' for 'ogg', 'libfdk_aac':'m4a',
          'pcm_s16le' for 16-bit wav and 'pcm_s32le' for 32-bit wav.
          Default is 'libmp3lame', unless the video extension is 'ogv'
          or 'webm', at which case the default is 'libvorbis'.

        audio_bitrate
          Audio bitrate, given as a string like '50k', '500k', '3000k'.
          Will determine the size/quality of audio in the output file.
          Note that it mainly an indicative goal, the bitrate won't
          necessarily be the this in the final file.

        preset
          Sets the time that FFMPEG will spend optimizing the compression.
          Choices are: ultrafast, superfast, veryfast, faster, fast, medium,
          slow, slower, veryslow, placebo. Note that this does not impact
          the quality of the video, only the size of the video file. So
          choose ultrafast when you are in a hurry and file size does not
          matter.

        threads
          Number of threads to use for ffmpeg. Can speed up the writing of
          the video on multicore computers.

        ffmpeg_params
          Any additional ffmpeg parameters you would like to pass, as a list
          of terms, like ['-option1', 'value1', '-option2', 'value2'].

        write_logfile
          If true, will write log files for the audio and the video.
          These will be files ending with '.log' with the name of the
          output file in them.

        logger
          Either "bar" for progress bar or None or any Proglog logger.

        verbose (deprecated, kept for compatibility)
          Formerly used for toggling messages on/off. Use logger=None now.

        Examples
        ========

        >>> from moviepy.editor import VideoFileClip
        >>> clip = VideoFileClip("myvideo.mp4").subclip(100,120)
        >>> clip.write_videofile("my_new_video.mp4")
        >>> clip.close()

        """
        name, ext = os.path.splitext(os.path.basename(filename))
        ext = ext[1:].lower()
        logger = proglog.default_bar_logger(logger)

        if codec is None:

            try:
                codec = extensions_dict[ext]['codec'][0]
            except KeyError:
                raise ValueError("MoviePy couldn't find the codec associated "
                                 "with the filename. Provide the 'codec' "
                                 "parameter in write_videofile.")

        if audio_codec is None:
            if ext in ['ogv', 'webm']:
                audio_codec = 'libvorbis'
            else:
                audio_codec = 'libmp3lame'
        elif audio_codec == 'raw16':
            audio_codec = 'pcm_s16le'
        elif audio_codec == 'raw32':
            audio_codec = 'pcm_s32le'

        audiofile = audio if is_string(audio) else None
        make_audio = ((audiofile is None) and (audio == True) and
                      (self.audio is not None))

        if make_audio and temp_audiofile:
            # The audio will be the clip's audio
            audiofile = temp_audiofile
        elif make_audio:
            audio_ext = find_extension(audio_codec)
            audiofile = (name + Clip._TEMP_FILES_PREFIX + "wvf_snd.%s" % audio_ext)

        # enough cpu for multiprocessing ? USELESS RIGHT NOW, WILL COME AGAIN
        # enough_cpu = (multiprocessing.cpu_count() > 1)
        logger(message="Moviepy - Building video %s." % filename)
        if make_audio:
            self.audio.write_audiofile(audiofile, audio_fps,
                                       audio_nbytes, audio_bufsize,
                                       audio_codec, bitrate=audio_bitrate,
                                       write_logfile=write_logfile,
                                       verbose=verbose,
                                       logger=logger)

        ffmpeg_write_video(self, filename, fps, codec,
                           bitrate=bitrate,
                           preset=preset,
                           write_logfile=write_logfile,
                           audiofile=audiofile,
                           verbose=verbose, threads=threads,
                           ffmpeg_params=ffmpeg_params,
                           logger=logger)

        if remove_temp and make_audio:
            if os.path.exists(audiofile):
                os.remove(audiofile)
        logger(message="Moviepy - video ready %s" % filename)