#
# The Python Imaging Library.
# $Id$
#
# the Image class wrapper
#
# partial release history:
# 1995-09-09 fl   Created
# 1996-03-11 fl   PIL release 0.0 (proof of concept)
# 1996-04-30 fl   PIL release 0.1b1
# 1999-07-28 fl   PIL release 1.0 final
# 2000-06-07 fl   PIL release 1.1
# 2000-10-20 fl   PIL release 1.1.1
# 2001-05-07 fl   PIL release 1.1.2
# 2002-03-15 fl   PIL release 1.1.3
# 2003-05-10 fl   PIL release 1.1.4
# 2005-03-28 fl   PIL release 1.1.5
# 2006-12-02 fl   PIL release 1.1.6
# 2009-11-15 fl   PIL release 1.1.7
#
# Copyright (c) 1997-2009 by Secret Labs AB.  All rights reserved.
# Copyright (c) 1995-2009 by Fredrik Lundh.
#
# See the README file for information on usage and redistribution.
#

import builtins
import io
import logging
import math
import os
import struct
import sys
import tempfile
import warnings
from enum import IntEnum
from pathlib import Path


# VERSION was removed in Pillow 6.0.0.
# PILLOW_VERSION was removed in Pillow 9.0.0.
# Use __version__ instead.
from . import (
    ImageMode,
    UnidentifiedImageError,
    __version__,
    _plugins,
)
from ._deprecate import deprecate
from ._util import DeferredError, is_path


def __getattr__(name):
    categories = {"NORMAL": 0, "SEQUENCE": 1, "CONTAINER": 2}
    if name in categories:
        deprecate("Image categories", 10, "is_animated", plural=True)
        return categories[name]
    old_resampling = {
        "LINEAR": "BILINEAR",
        "CUBIC": "BICUBIC",
        "ANTIALIAS": "LANCZOS",
    }
    if name in old_resampling:
        deprecate(
            name, 10, f"{old_resampling[name]} or Resampling.{old_resampling[name]}"
        )
        return Resampling[old_resampling[name]]
    msg = f"module '{__name__}' has no attribute '{name}'"
    raise AttributeError(msg)


logger = logging.getLogger('log')


class DecompressionBombWarning(RuntimeWarning):
    pass


class DecompressionBombError(Exception):
    pass


# Limit to around a quarter gigabyte for a 24-bit (3 bpp) image
MAX_IMAGE_PIXELS = int(1024 * 1024 * 1024 // 4 // 3)


try:
    # If the _imaging C module is not present, Pillow will not load.
    # Note that other modules should not refer to _imaging directly;
    # import Image and use the Image.core variable instead.
    # Also note that Image.core is not a publicly documented interface,
    # and should be considered private and subject to change.
    from . import _imaging as core

    '''
    if __version__ != getattr(core, "PILLOW_VERSION", None):
        msg = (
            "The _imaging extension was built for another version of Pillow or PIL:\n"
            f"Core version: {getattr(core, 'PILLOW_VERSION', None)}\n"
            f"Pillow version: {__version__}"
        )
        raise ImportError(msg)
    '''

except ImportError as v:
    core = DeferredError(ImportError("The _imaging C module is not installed."))
    # Explanations for ways that we know we might have an import error
    if str(v).startswith("Module use of python"):
        # The _imaging C module is present, but not compiled for
        # the right version (windows only).  Print a warning, if
        # possible.
        warnings.warn(
            "The _imaging extension was built for another version of Python.",
            RuntimeWarning,
        )
    elif str(v).startswith("The _imaging extension"):
        warnings.warn(str(v), RuntimeWarning)
    # Fail here anyway. Don't let people run with a mostly broken Pillow.
    # see docs/porting.rst
    raise


# works everywhere, win for pypy, not cpython
USE_CFFI_ACCESS = hasattr(sys, "pypy_version_info")
try:
    import cffi
except ImportError:
    cffi = None


def isImageType(t):
    """
    Checks if an object is an image object.

    .. warning::

       This function is for internal use only.

    :param t: object to check if it's an image
    :returns: True if the object is an image
    """
    return hasattr(t, "im")


#
# Constants


# transpose
class Transpose(IntEnum):
    FLIP_LEFT_RIGHT = 0
    FLIP_TOP_BOTTOM = 1
    ROTATE_90 = 2
    ROTATE_180 = 3
    ROTATE_270 = 4
    TRANSPOSE = 5
    TRANSVERSE = 6


# transforms (also defined in Imaging.h)
class Transform(IntEnum):
    AFFINE = 0
    EXTENT = 1
    PERSPECTIVE = 2
    QUAD = 3
    MESH = 4


# resampling filters (also defined in Imaging.h)
class Resampling(IntEnum):
    NEAREST = 0
    BOX = 4
    BILINEAR = 2
    HAMMING = 5
    BICUBIC = 3
    LANCZOS = 1


_filters_support = {
    Resampling.BOX: 0.5,
    Resampling.BILINEAR: 1.0,
    Resampling.HAMMING: 1.0,
    Resampling.BICUBIC: 2.0,
    Resampling.LANCZOS: 3.0,
}


# dithers
class Dither(IntEnum):
    NONE = 0
    ORDERED = 1  # Not yet implemented
    RASTERIZE = 2  # Not yet implemented
    FLOYDSTEINBERG = 3  # default


# palettes/quantizers
class Palette(IntEnum):
    WEB = 0
    ADAPTIVE = 1


class Quantize(IntEnum):
    MEDIANCUT = 0
    MAXCOVERAGE = 1
    FASTOCTREE = 2
    LIBIMAGEQUANT = 3


module = sys.modules[__name__]
for enum in (Transpose, Transform, Resampling, Dither, Palette, Quantize):
    for item in enum:
        setattr(module, item.name, item.value)


if hasattr(core, "DEFAULT_STRATEGY"):
    DEFAULT_STRATEGY = core.DEFAULT_STRATEGY
    FILTERED = core.FILTERED
    HUFFMAN_ONLY = core.HUFFMAN_ONLY
    RLE = core.RLE
    FIXED = core.FIXED


# --------------------------------------------------------------------
# Registries

ID = []
OPEN = {}
MIME = {}
SAVE = {}
SAVE_ALL = {}
EXTENSION = {}
DECODERS = {}
ENCODERS = {}

# --------------------------------------------------------------------
# Modes

_ENDIAN = "<" if sys.byteorder == "little" else ">"


def _conv_type_shape(im):
    m = ImageMode.getmode(im.mode)
    shape = (im.height, im.width)
    extra = len(m.bands)
    if extra != 1:
        shape += (extra,)
    return shape, m.typestr


MODES = ["1", "CMYK", "F", "HSV", "I", "L", "LAB", "P", "RGB", "RGBA", "RGBX", "YCbCr"]

# raw modes that may be memory mapped.  NOTE: if you change this, you
# may have to modify the stride calculation in map.c too!
_MAPMODES = ("L", "P", "RGBX", "RGBA", "CMYK", "I;16", "I;16L", "I;16B")


def getmodebase(mode):
    """
    Gets the "base" mode for given mode.  This function returns "L" for
    images that contain grayscale data, and "RGB" for images that
    contain color data.

    :param mode: Input mode.
    :returns: "L" or "RGB".
    :exception KeyError: If the input mode was not a standard mode.
    """
    return ImageMode.getmode(mode).basemode


def getmodetype(mode):
    """
    Gets the storage type mode.  Given a mode, this function returns a
    single-layer mode suitable for storing individual bands.

    :param mode: Input mode.
    :returns: "L", "I", or "F".
    :exception KeyError: If the input mode was not a standard mode.
    """
    return ImageMode.getmode(mode).basetype


def getmodebandnames(mode):
    """
    Gets a list of individual band names.  Given a mode, this function returns
    a tuple containing the names of individual bands (use
    :py:method:`~PIL.Image.getmodetype` to get the mode used to store each
    individual band.

    :param mode: Input mode.
    :returns: A tuple containing band names.  The length of the tuple
        gives the number of bands in an image of the given mode.
    :exception KeyError: If the input mode was not a standard mode.
    """
    return ImageMode.getmode(mode).bands


def getmodebands(mode):
    """
    Gets the number of individual bands for this mode.

    :param mode: Input mode.
    :returns: The number of bands in this mode.
    :exception KeyError: If the input mode was not a standard mode.
    """
    return len(ImageMode.getmode(mode).bands)


# --------------------------------------------------------------------
# Helpers

_initialized = 0


def preinit():
    """Explicitly load standard file format drivers."""

    global _initialized
    if _initialized >= 1:
        return

    try:
        from . import BmpImagePlugin

        assert BmpImagePlugin
    except ImportError:
        pass
    try:
        from . import GifImagePlugin

        assert GifImagePlugin
    except ImportError:
        pass
    try:
        from . import JpegImagePlugin

        assert JpegImagePlugin
    except ImportError as e:
        print(e.msg)
        # pass
    try:
        from . import PpmImagePlugin

        assert PpmImagePlugin
    except ImportError:
        pass
    try:
        from . import PngImagePlugin

        assert PngImagePlugin
    except ImportError as e:
        print(e.msg)
        # pass
    # try:
    #     import TiffImagePlugin
    #     assert TiffImagePlugin
    # except ImportError:
    #     pass

    _initialized = 1


def init():
    """
    Explicitly initializes the Python Imaging Library. This function
    loads all available file format drivers.
    """

    global _initialized
    if _initialized >= 2:
        return 0

    for plugin in _plugins:
        try:
            logger.debug("Importing %s", plugin)
            __import__(f"PIL.{plugin}", globals(), locals(), [])
        except ImportError as e:
            logger.debug("Image: failed to import %s: %s", plugin, e)

    if OPEN or SAVE:
        _initialized = 2
        return 1


# --------------------------------------------------------------------
# Codec factories (used by tobytes/frombytes and ImageFile.load)


def _getdecoder(mode, decoder_name, args, extra=()):
    # tweak arguments
    if args is None:
        args = ()
    elif not isinstance(args, tuple):
        args = (args,)

    try:
        decoder = DECODERS[decoder_name]
    except KeyError:
        pass
    else:
        return decoder(mode, *args + extra)

    try:
        # get decoder
        decoder = getattr(core, decoder_name + "_decoder")
    except AttributeError as e:
        msg = f"decoder {decoder_name} not available"
        raise OSError(msg) from e
    return decoder(mode, *args + extra)


def _getencoder(mode, encoder_name, args, extra=()):
    # tweak arguments
    if args is None:
        args = ()
    elif not isinstance(args, tuple):
        args = (args,)

    try:
        encoder = ENCODERS[encoder_name]
    except KeyError:
        pass
    else:
        return encoder(mode, *args + extra)

    try:
        # get encoder
        encoder = getattr(core, encoder_name + "_encoder")
    except AttributeError as e:
        msg = f"encoder {encoder_name} not available"
        raise OSError(msg) from e
    return encoder(mode, *args + extra)


# --------------------------------------------------------------------
# Simple expression analyzer


def coerce_e(value):
    deprecate("coerce_e", 10)
    return value if isinstance(value, _E) else _E(1, value)


# _E(scale, offset) represents the affine transformation scale * x + offset.
# The "data" field is named for compatibility with the old implementation,
# and should be renamed once coerce_e is removed.
class _E:
    def __init__(self, scale, data):
        self.scale = scale
        self.data = data

    def __neg__(self):
        return _E(-self.scale, -self.data)

    def __add__(self, other):
        if isinstance(other, _E):
            return _E(self.scale + other.scale, self.data + other.data)
        return _E(self.scale, self.data + other)

    __radd__ = __add__

    def __sub__(self, other):
        return self + -other

    def __rsub__(self, other):
        return other + -self

    def __mul__(self, other):
        if isinstance(other, _E):
            return NotImplemented
        return _E(self.scale * other, self.data * other)

    __rmul__ = __mul__

    def __truediv__(self, other):
        if isinstance(other, _E):
            return NotImplemented
        return _E(self.scale / other, self.data / other)


def _getscaleoffset(expr):
    a = expr(_E(1, 0))
    return (a.scale, a.data) if isinstance(a, _E) else (0, a)


# --------------------------------------------------------------------
# Implementation wrapper


class Image:
    """
    This class represents an image object.  To create
    :py:class:`~PIL.Image.Image` objects, use the appropriate factory
    functions.  There's hardly ever any reason to call the Image constructor
    directly.

    * :py:func:`~PIL.Image.open`
    * :py:func:`~PIL.Image.new`
    * :py:func:`~PIL.Image.frombytes`
    """

    format = None
    format_description = None
    _close_exclusive_fp_after_loading = True

    def __init__(self):
        # FIXME: take "new" parameters / other image?
        # FIXME: turn mode and size into delegating properties?
        self.im = None
        self.mode = ""
        self._size = (0, 0)
        self.palette = None
        self.info = {}
        self._category = 0
        self.readonly = 0
        self.pyaccess = None
        self._exif = None

    def __getattr__(self, name):
        if name == "category":
            deprecate("Image categories", 10, "is_animated", plural=True)
            return self._category
        raise AttributeError(name)

    @property
    def width(self):
        return self.size[0]

    @property
    def height(self):
        return self.size[1]

    @property
    def size(self):
        return self._size

    def _new(self, im):
        new = Image()
        new.im = im
        new.mode = im.mode
        new._size = im.size
        if im.mode in ("P", "PA"):
            if self.palette:
                new.palette = self.palette.copy()
            else:
                from . import ImagePalette

                new.palette = ImagePalette.ImagePalette()
        new.info = self.info.copy()
        return new

    # Context manager support
    def __enter__(self):
        return self

    def __exit__(self, *args):
        if hasattr(self, "fp") and getattr(self, "_exclusive_fp", False):
            if getattr(self, "_fp", False):
                if self._fp != self.fp:
                    self._fp.close()
                self._fp = DeferredError(ValueError("Operation on closed image"))
            if self.fp:
                self.fp.close()
        self.fp = None

    def close(self):
        """
        Closes the file pointer, if possible.

        This operation will destroy the image core and release its memory.
        The image data will be unusable afterward.

        This function is required to close images that have multiple frames or
        have not had their file read and closed by the
        :py:meth:`~PIL.Image.Image.load` method. See :ref:`file-handling` for
        more information.
        """
        try:
            if getattr(self, "_fp", False):
                if self._fp != self.fp:
                    self._fp.close()
                self._fp = DeferredError(ValueError("Operation on closed image"))
            if self.fp:
                self.fp.close()
            self.fp = None
        except Exception as msg:
            logger.debug("Error closing: %s", msg)

        if getattr(self, "map", None):
            self.map = None

        # Instead of simply setting to None, we're setting up a
        # deferred error that will better explain that the core image
        # object is gone.
        self.im = DeferredError(ValueError("Operation on closed image"))

    def _copy(self):
        self.load()
        self.im = self.im.copy()
        self.pyaccess = None
        self.readonly = 0

    def _ensure_mutable(self):
        if self.readonly:
            self._copy()
        else:
            self.load()

    def _dump(self, file=None, format=None, **options):
        suffix = ""
        if format:
            suffix = "." + format

        if not file:
            f, filename = tempfile.mkstemp(suffix)
            os.close(f)
        else:
            filename = file
            if not filename.endswith(suffix):
                filename = filename + suffix

        self.load()

        if not format or format == "PPM":
            self.im.save_ppm(filename)
        else:
            self.save(filename, format, **options)

        return filename

    def __eq__(self, other):
        return (
            self.__class__ is other.__class__
            and self.mode == other.mode
            and self.size == other.size
            and self.info == other.info
            and self._category == other._category
            and self.getpalette() == other.getpalette()
            and self.tobytes() == other.tobytes()
        )

    def __repr__(self):
        return "<%s.%s image mode=%s size=%dx%d at 0x%X>" % (
            self.__class__.__module__,
            self.__class__.__name__,
            self.mode,
            self.size[0],
            self.size[1],
            id(self),
        )

    def _repr_pretty_(self, p, cycle):
        """IPython plain text display support"""

        # Same as __repr__ but without unpredictable id(self),
        # to keep Jupyter notebook `text/plain` output stable.
        p.text(
            "<%s.%s image mode=%s size=%dx%d>"
            % (
                self.__class__.__module__,
                self.__class__.__name__,
                self.mode,
                self.size[0],
                self.size[1],
            )
        )

    def _repr_png_(self):
        """iPython display hook support

        :returns: png version of the image as bytes
        """
        b = io.BytesIO()
        try:
            self.save(b, "PNG")
        except Exception as e:
            msg = "Could not save to PNG for display"
            raise ValueError(msg) from e
        return b.getvalue()

    @property
    def __array_interface__(self):
        # numpy array interface support
        new = {"version": 3}
        try:
            if self.mode == "1":
                # Binary images need to be extended from bits to bytes
                # See: https://github.com/python-pillow/Pillow/issues/350
                new["data"] = self.tobytes("raw", "L")
            else:
                new["data"] = self.tobytes()
        except Exception as e:
            if not isinstance(e, (MemoryError, RecursionError)):
                try:
                    import numpy
                    from packaging.version import parse as parse_version
                except ImportError:
                    pass
                else:
                    if parse_version(numpy.__version__) < parse_version("1.23"):
                        warnings.warn(e)
            raise
        new["shape"], new["typestr"] = _conv_type_shape(self)
        return new

    def __getstate__(self):
        return [self.info, self.mode, self.size, self.getpalette(), self.tobytes()]

    def __setstate__(self, state):
        Image.__init__(self)
        info, mode, size, palette, data = state
        self.info = info
        self.mode = mode
        self._size = size
        self.im = core.new(mode, size)
        if mode in ("L", "LA", "P", "PA") and palette:
            self.putpalette(palette)
        self.frombytes(data)

    def tobytes(self, encoder_name="raw", *args):
        """
        Return image as a bytes object.

        .. warning::

            This method returns the raw image data from the internal
            storage.  For compressed image data (e.g. PNG, JPEG) use
            :meth:`~.save`, with a BytesIO parameter for in-memory
            data.

        :param encoder_name: What encoder to use.  The default is to
                             use the standard "raw" encoder.

                             A list of C encoders can be seen under
                             codecs section of the function array in
                             :file:`_imaging.c`. Python encoders are
                             registered within the relevant plugins.
        :param args: Extra arguments to the encoder.
        :returns: A :py:class:`bytes` object.
        """

        # may pass tuple instead of argument list
        if len(args) == 1 and isinstance(args[0], tuple):
            args = args[0]

        if encoder_name == "raw" and args == ():
            args = self.mode

        self.load()

        if self.width == 0 or self.height == 0:
            return b""

        # unpack data
        e = _getencoder(self.mode, encoder_name, args)
        e.setimage(self.im)

        bufsize = max(65536, self.size[0] * 4)  # see RawEncode.c

        output = []
        while True:
            bytes_consumed, errcode, data = e.encode(bufsize)
            output.append(data)
            if errcode:
                break
        if errcode < 0:
            msg = f"encoder error {errcode} in tobytes"
            raise RuntimeError(msg)

        return b"".join(output)

    def tobitmap(self, name="image"):
        """
        Returns the image converted to an X11 bitmap.

        .. note:: This method only works for mode "1" images.

        :param name: The name prefix to use for the bitmap variables.
        :returns: A string containing an X11 bitmap.
        :raises ValueError: If the mode is not "1"
        """

        self.load()
        if self.mode != "1":
            msg = "not a bitmap"
            raise ValueError(msg)
        data = self.tobytes("xbm")
        return b"".join(
            [
                f"#define {name}_width {self.size[0]}\n".encode("ascii"),
                f"#define {name}_height {self.size[1]}\n".encode("ascii"),
                f"static char {name}_bits[] = {{\n".encode("ascii"),
                data,
                b"};",
            ]
        )

    def frombytes(self, data, decoder_name="raw", *args):
        """
        Loads this image with pixel data from a bytes object.

        This method is similar to the :py:func:`~PIL.Image.frombytes` function,
        but loads data into this image instead of creating a new image object.
        """

        # may pass tuple instead of argument list
        if len(args) == 1 and isinstance(args[0], tuple):
            args = args[0]

        # default format
        if decoder_name == "raw" and args == ():
            args = self.mode

        # unpack data
        d = _getdecoder(self.mode, decoder_name, args)
        d.setimage(self.im)
        s = d.decode(data)

        if s[0] >= 0:
            msg = "not enough image data"
            raise ValueError(msg)
        if s[1] != 0:
            msg = "cannot decode image data"
            raise ValueError(msg)

    def load(self):
        """
        Allocates storage for the image and loads the pixel data.  In
        normal cases, you don't need to call this method, since the
        Image class automatically loads an opened image when it is
        accessed for the first time.

        If the file associated with the image was opened by Pillow, then this
        method will close it. The exception to this is if the image has
        multiple frames, in which case the file will be left open for seek
        operations. See :ref:`file-handling` for more information.

        :returns: An image access object.
        :rtype: :ref:`PixelAccess` or :py:class:`PIL.PyAccess`
        """
        logger.info('into load')
        if self.im is not None and self.palette and self.palette.dirty:
            # realize palette
            mode, arr = self.palette.getdata()
            self.im.putpalette(mode, arr)
            self.palette.dirty = 0
            self.palette.rawmode = None
            if "transparency" in self.info and mode in ("LA", "PA"):
                if isinstance(self.info["transparency"], int):
                    self.im.putpalettealpha(self.info["transparency"], 0)
                else:
                    self.im.putpalettealphas(self.info["transparency"])
                self.palette.mode = "RGBA"
            else:
                palette_mode = "RGBA" if mode.startswith("RGBA") else "RGB"
                self.palette.mode = palette_mode
                self.palette.palette = self.im.getpalette(palette_mode, palette_mode)

        if self.im is not None:
            if cffi and USE_CFFI_ACCESS:
                if self.pyaccess:
                    return self.pyaccess
                from . import PyAccess

                self.pyaccess = PyAccess.new(self, self.readonly)
                if self.pyaccess:
                    return self.pyaccess
            return self.im.pixel_access(self.readonly)

    def verify(self):
        """
        Verifies the contents of a file. For data read from a file, this
        method attempts to determine if the file is broken, without
        actually decoding the image data.  If this method finds any
        problems, it raises suitable exceptions.  If you need to load
        the image after using this method, you must reopen the image
        file.
        """
        pass

    def convert(
        self, mode=None, matrix=None, dither=None, palette=Palette.WEB, colors=256
    ):
        """
        Returns a converted copy of this image. For the "P" mode, this
        method translates pixels through the palette.  If mode is
        omitted, a mode is chosen so that all information in the image
        and the palette can be represented without a palette.

        The current version supports all possible conversions between
        "L", "RGB" and "CMYK". The ``matrix`` argument only supports "L"
        and "RGB".

        When translating a color image to greyscale (mode "L"),
        the library uses the ITU-R 601-2 luma transform::

            L = R * 299/1000 + G * 587/1000 + B * 114/1000

        The default method of converting a greyscale ("L") or "RGB"
        image into a bilevel (mode "1") image uses Floyd-Steinberg
        dither to approximate the original image luminosity levels. If
        dither is ``None``, all values larger than 127 are set to 255 (white),
        all other values to 0 (black). To use other thresholds, use the
        :py:meth:`~PIL.Image.Image.point` method.

        When converting from "RGBA" to "P" without a ``matrix`` argument,
        this passes the operation to :py:meth:`~PIL.Image.Image.quantize`,
        and ``dither`` and ``palette`` are ignored.

        When converting from "PA", if an "RGBA" palette is present, the alpha
        channel from the image will be used instead of the values from the palette.

        :param mode: The requested mode. See: :ref:`concept-modes`.
        :param matrix: An optional conversion matrix.  If given, this
           should be 4- or 12-tuple containing floating point values.
        :param dither: Dithering method, used when converting from
           mode "RGB" to "P" or from "RGB" or "L" to "1".
           Available methods are :data:`Dither.NONE` or :data:`Dither.FLOYDSTEINBERG`
           (default). Note that this is not used when ``matrix`` is supplied.
        :param palette: Palette to use when converting from mode "RGB"
           to "P".  Available palettes are :data:`Palette.WEB` or
           :data:`Palette.ADAPTIVE`.
        :param colors: Number of colors to use for the :data:`Palette.ADAPTIVE`
           palette. Defaults to 256.
        :rtype: :py:class:`~PIL.Image.Image`
        :returns: An :py:class:`~PIL.Image.Image` object.
        """

        self.load()

        has_transparency = self.info.get("transparency") is not None
        if not mode and self.mode == "P":
            # determine default mode
            if self.palette:
                mode = self.palette.mode
            else:
                mode = "RGB"
            if mode == "RGB" and has_transparency:
                mode = "RGBA"
        if not mode or (mode == self.mode and not matrix):
            return self.copy()

        if matrix:
            # matrix conversion
            if mode not in ("L", "RGB"):
                msg = "illegal conversion"
                raise ValueError(msg)
            im = self.im.convert_matrix(mode, matrix)
            new = self._new(im)
            if has_transparency and self.im.bands == 3:
                transparency = new.info["transparency"]

                def convert_transparency(m, v):
                    v = m[0] * v[0] + m[1] * v[1] + m[2] * v[2] + m[3] * 0.5
                    return max(0, min(255, int(v)))

                if mode == "L":
                    transparency = convert_transparency(matrix, transparency)
                elif len(mode) == 3:
                    transparency = tuple(
                        convert_transparency(matrix[i * 4 : i * 4 + 4], transparency)
                        for i in range(0, len(transparency))
                    )
                new.info["transparency"] = transparency
            return new

        if mode == "P" and self.mode == "RGBA":
            return self.quantize(colors)

        trns = None
        delete_trns = False
        # transparency handling
        if has_transparency:
            if (self.mode in ("1", "L", "I") and mode in ("LA", "RGBA")) or (
                self.mode == "RGB" and mode == "RGBA"
            ):
                # Use transparent conversion to promote from transparent
                # color to an alpha channel.
                new_im = self._new(
                    self.im.convert_transparent(mode, self.info["transparency"])
                )
                del new_im.info["transparency"]
                return new_im
            elif self.mode in ("L", "RGB", "P") and mode in ("L", "RGB", "P"):
                t = self.info["transparency"]
                if isinstance(t, bytes):
                    # Dragons. This can't be represented by a single color
                    warnings.warn(
                        "Palette images with Transparency expressed in bytes should be "
                        "converted to RGBA images"
                    )
                    delete_trns = True
                else:
                    # get the new transparency color.
                    # use existing conversions
                    trns_im = Image()._new(core.new(self.mode, (1, 1)))
                    if self.mode == "P":
                        trns_im.putpalette(self.palette)
                        if isinstance(t, tuple):
                            err = "Couldn't allocate a palette color for transparency"
                            try:
                                t = trns_im.palette.getcolor(t, self)
                            except ValueError as e:
                                if str(e) == "cannot allocate more than 256 colors":
                                    # If all 256 colors are in use,
                                    # then there is no need for transparency
                                    t = None
                                else:
                                    raise ValueError(err) from e
                    if t is None:
                        trns = None
                    else:
                        trns_im.putpixel((0, 0), t)

                        if mode in ("L", "RGB"):
                            trns_im = trns_im.convert(mode)
                        else:
                            # can't just retrieve the palette number, got to do it
                            # after quantization.
                            trns_im = trns_im.convert("RGB")
                        trns = trns_im.getpixel((0, 0))

            elif self.mode == "P" and mode in ("LA", "PA", "RGBA"):
                t = self.info["transparency"]
                delete_trns = True

                if isinstance(t, bytes):
                    self.im.putpalettealphas(t)
                elif isinstance(t, int):
                    self.im.putpalettealpha(t, 0)
                else:
                    msg = "Transparency for P mode should be bytes or int"
                    raise ValueError(msg)

        if mode == "P" and palette == Palette.ADAPTIVE:
            im = self.im.quantize(colors)
            new = self._new(im)
            from . import ImagePalette

            new.palette = ImagePalette.ImagePalette("RGB", new.im.getpalette("RGB"))
            if delete_trns:
                # This could possibly happen if we requantize to fewer colors.
                # The transparency would be totally off in that case.
                del new.info["transparency"]
            if trns is not None:
                try:
                    new.info["transparency"] = new.palette.getcolor(trns, new)
                except Exception:
                    # if we can't make a transparent color, don't leave the old
                    # transparency hanging around to mess us up.
                    del new.info["transparency"]
                    warnings.warn("Couldn't allocate palette entry for transparency")
            return new

        if "LAB" in (self.mode, mode):
            other_mode = mode if self.mode == "LAB" else self.mode
            if other_mode in ("RGB", "RGBA", "RGBX"):
                from . import ImageCms

                srgb = ImageCms.createProfile("sRGB")
                lab = ImageCms.createProfile("LAB")
                profiles = [lab, srgb] if self.mode == "LAB" else [srgb, lab]
                transform = ImageCms.buildTransform(
                    profiles[0], profiles[1], self.mode, mode
                )
                return transform.apply(self)

        # colorspace conversion
        if dither is None:
            dither = Dither.FLOYDSTEINBERG

        try:
            im = self.im.convert(mode, dither)
        except ValueError:
            try:
                # normalize source image and try again
                modebase = getmodebase(self.mode)
                if modebase == self.mode:
                    raise
                im = self.im.convert(modebase)
                im = im.convert(mode, dither)
            except KeyError as e:
                msg = "illegal conversion"
                raise ValueError(msg) from e

        new_im = self._new(im)
        if mode == "P" and palette != Palette.ADAPTIVE:
            from . import ImagePalette

            new_im.palette = ImagePalette.ImagePalette("RGB", list(range(256)) * 3)
        if delete_trns:
            # crash fail if we leave a bytes transparency in an rgb/l mode.
            del new_im.info["transparency"]
        if trns is not None:
            if new_im.mode == "P":
                try:
                    new_im.info["transparency"] = new_im.palette.getcolor(trns, new_im)
                except ValueError as e:
                    del new_im.info["transparency"]
                    if str(e) != "cannot allocate more than 256 colors":
                        # If all 256 colors are in use,
                        # then there is no need for transparency
                        warnings.warn(
                            "Couldn't allocate palette entry for transparency"
                        )
            else:
                new_im.info["transparency"] = trns
        return new_im

    def quantize(
        self,
        colors=256,
        method=None,
        kmeans=0,
        palette=None,
        dither=Dither.FLOYDSTEINBERG,
    ):
        """
        Convert the image to 'P' mode with the specified number
        of colors.

        :param colors: The desired number of colors, <= 256
        :param method: :data:`Quantize.MEDIANCUT` (median cut),
                       :data:`Quantize.MAXCOVERAGE` (maximum coverage),
                       :data:`Quantize.FASTOCTREE` (fast octree),
                       :data:`Quantize.LIBIMAGEQUANT` (libimagequant; check support
                       using :py:func:`PIL.features.check_feature` with
                       ``feature="libimagequant"``).

                       By default, :data:`Quantize.MEDIANCUT` will be used.

                       The exception to this is RGBA images. :data:`Quantize.MEDIANCUT`
                       and :data:`Quantize.MAXCOVERAGE` do not support RGBA images, so
                       :data:`Quantize.FASTOCTREE` is used by default instead.
        :param kmeans: Integer
        :param palette: Quantize to the palette of given
                        :py:class:`PIL.Image.Image`.
        :param dither: Dithering method, used when converting from
           mode "RGB" to "P" or from "RGB" or "L" to "1".
           Available methods are :data:`Dither.NONE` or :data:`Dither.FLOYDSTEINBERG`
           (default).
        :returns: A new image

        """

        self.load()

        if method is None:
            # defaults:
            method = Quantize.MEDIANCUT
            if self.mode == "RGBA":
                method = Quantize.FASTOCTREE

        if self.mode == "RGBA" and method not in (
            Quantize.FASTOCTREE,
            Quantize.LIBIMAGEQUANT,
        ):
            # Caller specified an invalid mode.
            msg = (
                "Fast Octree (method == 2) and libimagequant (method == 3) "
                "are the only valid methods for quantizing RGBA images"
            )
            raise ValueError(msg)

        if palette:
            # use palette from reference image
            palette.load()
            if palette.mode != "P":
                msg = "bad mode for palette image"
                raise ValueError(msg)
            if self.mode != "RGB" and self.mode != "L":
                msg = "only RGB or L mode images can be quantized to a palette"
                raise ValueError(msg)
            im = self.im.convert("P", dither, palette.im)
            new_im = self._new(im)
            new_im.palette = palette.palette.copy()
            return new_im

        im = self._new(self.im.quantize(colors, method, kmeans))

        from . import ImagePalette

        mode = im.im.getpalettemode()
        palette = im.im.getpalette(mode, mode)[: colors * len(mode)]
        im.palette = ImagePalette.ImagePalette(mode, palette)

        return im

    def copy(self):
        """
        Copies this image. Use this method if you wish to paste things
        into an image, but still retain the original.

        :rtype: :py:class:`~PIL.Image.Image`
        :returns: An :py:class:`~PIL.Image.Image` object.
        """
        self.load()
        return self._new(self.im.copy())

    __copy__ = copy



    def _crop(self, im, box):
        """
        Returns a rectangular region from the core image object im.

        This is equivalent to calling im.crop((x0, y0, x1, y1)), but
        includes additional sanity checks.

        :param im: a core image object
        :param box: The crop rectangle, as a (left, upper, right, lower)-tuple.
        :returns: A core image object.
        """

        x0, y0, x1, y1 = map(int, map(round, box))

        absolute_values = (abs(x1 - x0), abs(y1 - y0))

        _decompression_bomb_check(absolute_values)

        return im.crop((x0, y0, x1, y1))


    def _expand(self, xmargin, ymargin=None):
        if ymargin is None:
            ymargin = xmargin
        self.load()
        return self._new(self.im.expand(xmargin, ymargin, 0))


    def alpha_composite(self, im, dest=(0, 0), source=(0, 0)):
        """'In-place' analog of Image.alpha_composite. Composites an image
        onto this image.

        :param im: image to composite over this one
        :param dest: Optional 2 tuple (left, top) specifying the upper
          left corner in this (destination) image.
        :param source: Optional 2 (left, top) tuple for the upper left
          corner in the overlay source image, or 4 tuple (left, top, right,
          bottom) for the bounds of the source rectangle

        Performance Note: Not currently implemented in-place in the core layer.
        """

        if not isinstance(source, (list, tuple)):
            msg = "Source must be a tuple"
            raise ValueError(msg)
        if not isinstance(dest, (list, tuple)):
            msg = "Destination must be a tuple"
            raise ValueError(msg)
        if not len(source) in (2, 4):
            msg = "Source must be a 2 or 4-tuple"
            raise ValueError(msg)
        if not len(dest) == 2:
            msg = "Destination must be a 2-tuple"
            raise ValueError(msg)
        if min(source) < 0:
            msg = "Source must be non-negative"
            raise ValueError(msg)

        if len(source) == 2:
            source = source + im.size

        # over image, crop if it's not the whole thing.
        if source == (0, 0) + im.size:
            overlay = im
        else:
            overlay = im.crop(source)

        # target for the paste
        box = dest + (dest[0] + overlay.width, dest[1] + overlay.height)

        # destination image. don't copy if we're using the whole image.
        if box == (0, 0) + self.size:
            background = self
        else:
            background = self.crop(box)

        result = alpha_composite(background, overlay)
        self.paste(result, box)

    def point(self, lut, mode=None):
        """
        Maps this image through a lookup table or function.

        :param lut: A lookup table, containing 256 (or 65536 if
           self.mode=="I" and mode == "L") values per band in the
           image.  A function can be used instead, it should take a
           single argument. The function is called once for each
           possible pixel value, and the resulting table is applied to
           all bands of the image.

           It may also be an :py:class:`~PIL.Image.ImagePointHandler`
           object::

               class Example(Image.ImagePointHandler):
                 def point(self, data):
                   # Return result
        :param mode: Output mode (default is same as input).  In the
           current version, this can only be used if the source image
           has mode "L" or "P", and the output has mode "1" or the
           source image mode is "I" and the output mode is "L".
        :returns: An :py:class:`~PIL.Image.Image` object.
        """

        self.load()

        if isinstance(lut, ImagePointHandler):
            return lut.point(self)

        if callable(lut):
            # if it isn't a list, it should be a function
            if self.mode in ("I", "I;16", "F"):
                # check if the function can be used with point_transform
                # UNDONE wiredfool -- I think this prevents us from ever doing
                # a gamma function point transform on > 8bit images.
                scale, offset = _getscaleoffset(lut)
                return self._new(self.im.point_transform(scale, offset))
            # for other modes, convert the function to a table
            lut = [lut(i) for i in range(256)] * self.im.bands

        if self.mode == "F":
            # FIXME: _imaging returns a confusing error message for this case
            msg = "point operation not supported for this mode"
            raise ValueError(msg)

        if mode != "F":
            lut = [round(i) for i in lut]
        return self._new(self.im.point(lut, mode))

    def putalpha(self, alpha):
        """
        Adds or replaces the alpha layer in this image.  If the image
        does not have an alpha layer, it's converted to "LA" or "RGBA".
        The new layer must be either "L" or "1".

        :param alpha: The new alpha layer.  This can either be an "L" or "1"
           image having the same size as this image, or an integer or
           other color value.
        """

        self._ensure_mutable()

        if self.mode not in ("LA", "PA", "RGBA"):
            # attempt to promote self to a matching alpha mode
            try:
                mode = getmodebase(self.mode) + "A"
                try:
                    self.im.setmode(mode)
                except (AttributeError, ValueError) as e:
                    # do things the hard way
                    im = self.im.convert(mode)
                    if im.mode not in ("LA", "PA", "RGBA"):
                        raise ValueError from e  # sanity check
                    self.im = im
                self.pyaccess = None
                self.mode = self.im.mode
            except KeyError as e:
                msg = "illegal image mode"
                raise ValueError(msg) from e

        if self.mode in ("LA", "PA"):
            band = 1
        else:
            band = 3

        if isImageType(alpha):
            # alpha layer
            if alpha.mode not in ("1", "L"):
                msg = "illegal image mode"
                raise ValueError(msg)
            alpha.load()
            if alpha.mode == "1":
                alpha = alpha.convert("L")
        else:
            # constant alpha
            try:
                self.im.fillband(band, alpha)
            except (AttributeError, ValueError):
                # do things the hard way
                alpha = new("L", self.size, alpha)
            else:
                return

        self.im.putband(alpha.im, band)

    def putdata(self, data, scale=1.0, offset=0.0):
        """
        Copies pixel data from a flattened sequence object into the image. The
        values should start at the upper left corner (0, 0), continue to the
        end of the line, followed directly by the first value of the second
        line, and so on. Data will be read until either the image or the
        sequence ends. The scale and offset values are used to adjust the
        sequence values: **pixel = value*scale + offset**.

        :param data: A flattened sequence object.
        :param scale: An optional scale value.  The default is 1.0.
        :param offset: An optional offset value.  The default is 0.0.
        """

        self._ensure_mutable()

        self.im.putdata(data, scale, offset)


    def _get_safe_box(self, size, resample, box):
        """Expands the box so it includes adjacent pixels
        that may be used by resampling with the given resampling filter.
        """
        filter_support = _filters_support[resample] - 0.5
        scale_x = (box[2] - box[0]) / size[0]
        scale_y = (box[3] - box[1]) / size[1]
        support_x = filter_support * scale_x
        support_y = filter_support * scale_y

        return (
            max(0, int(box[0] - support_x)),
            max(0, int(box[1] - support_y)),
            min(self.size[0], math.ceil(box[2] + support_x)),
            min(self.size[1], math.ceil(box[3] + support_y)),
        )

    def resize(self, size, resample=None, box=None, reducing_gap=None):
        """
        Returns a resized copy of this image.

        :param size: The requested size in pixels, as a 2-tuple:
           (width, height).
        :param resample: An optional resampling filter.  This can be
           one of :py:data:`Resampling.NEAREST`, :py:data:`Resampling.BOX`,
           :py:data:`Resampling.BILINEAR`, :py:data:`Resampling.HAMMING`,
           :py:data:`Resampling.BICUBIC` or :py:data:`Resampling.LANCZOS`.
           If the image has mode "1" or "P", it is always set to
           :py:data:`Resampling.NEAREST`. If the image mode specifies a number
           of bits, such as "I;16", then the default filter is
           :py:data:`Resampling.NEAREST`. Otherwise, the default filter is
           :py:data:`Resampling.BICUBIC`. See: :ref:`concept-filters`.
        :param box: An optional 4-tuple of floats providing
           the source image region to be scaled.
           The values must be within (0, 0, width, height) rectangle.
           If omitted or None, the entire source is used.
        :param reducing_gap: Apply optimization by resizing the image
           in two steps. First, reducing the image by integer times
           using :py:meth:`~PIL.Image.Image.reduce`.
           Second, resizing using regular resampling. The last step
           changes size no less than by ``reducing_gap`` times.
           ``reducing_gap`` may be None (no first step is performed)
           or should be greater than 1.0. The bigger ``reducing_gap``,
           the closer the result to the fair resampling.
           The smaller ``reducing_gap``, the faster resizing.
           With ``reducing_gap`` greater or equal to 3.0, the result is
           indistinguishable from fair resampling in most cases.
           The default value is None (no optimization).
        :returns: An :py:class:`~PIL.Image.Image` object.
        """

        if resample is None:
            type_special = ";" in self.mode
            resample = Resampling.NEAREST if type_special else Resampling.BICUBIC
        elif resample not in (
            Resampling.NEAREST,
            Resampling.BILINEAR,
            Resampling.BICUBIC,
            Resampling.LANCZOS,
            Resampling.BOX,
            Resampling.HAMMING,
        ):
            msg = f"Unknown resampling filter ({resample})."

            filters = [
                f"{filter[1]} ({filter[0]})"
                for filter in (
                    (Resampling.NEAREST, "Image.Resampling.NEAREST"),
                    (Resampling.LANCZOS, "Image.Resampling.LANCZOS"),
                    (Resampling.BILINEAR, "Image.Resampling.BILINEAR"),
                    (Resampling.BICUBIC, "Image.Resampling.BICUBIC"),
                    (Resampling.BOX, "Image.Resampling.BOX"),
                    (Resampling.HAMMING, "Image.Resampling.HAMMING"),
                )
            ]
            msg += " Use " + ", ".join(filters[:-1]) + " or " + filters[-1]
            raise ValueError(msg)

        if reducing_gap is not None and reducing_gap < 1.0:
            msg = "reducing_gap must be 1.0 or greater"
            raise ValueError(msg)

        size = tuple(size)

        self.load()
        if box is None:
            box = (0, 0) + self.size
        else:
            box = tuple(box)

        if self.size == size and box == (0, 0) + self.size:
            return self.copy()

        if self.mode in ("1", "P"):
            resample = Resampling.NEAREST

        if self.mode in ["LA", "RGBA"] and resample != Resampling.NEAREST:
            im = self.convert({"LA": "La", "RGBA": "RGBa"}[self.mode])
            im = im.resize(size, resample, box)
            return im.convert(self.mode)

        self.load()

        if reducing_gap is not None and resample != Resampling.NEAREST:
            factor_x = int((box[2] - box[0]) / size[0] / reducing_gap) or 1
            factor_y = int((box[3] - box[1]) / size[1] / reducing_gap) or 1
            if factor_x > 1 or factor_y > 1:
                reduce_box = self._get_safe_box(size, resample, box)
                factor = (factor_x, factor_y)
                if callable(self.reduce):
                    self = self.reduce(factor, box=reduce_box)
                else:
                    self = Image.reduce(self, factor, box=reduce_box)
                box = (
                    (box[0] - reduce_box[0]) / factor_x,
                    (box[1] - reduce_box[1]) / factor_y,
                    (box[2] - reduce_box[0]) / factor_x,
                    (box[3] - reduce_box[1]) / factor_y,
                )

        return self._new(self.im.resize(size, resample, box))

    def reduce(self, factor, box=None):
        """
        Returns a copy of the image reduced ``factor`` times.
        If the size of the image is not dividable by ``factor``,
        the resulting size will be rounded up.

        :param factor: A greater than 0 integer or tuple of two integers
           for width and height separately.
        :param box: An optional 4-tuple of ints providing
           the source image region to be reduced.
           The values must be within ``(0, 0, width, height)`` rectangle.
           If omitted or ``None``, the entire source is used.
        """
        if not isinstance(factor, (list, tuple)):
            factor = (factor, factor)

        if box is None:
            box = (0, 0) + self.size
        else:
            box = tuple(box)

        if factor == (1, 1) and box == (0, 0) + self.size:
            return self.copy()

        if self.mode in ["LA", "RGBA"]:
            im = self.convert({"LA": "La", "RGBA": "RGBa"}[self.mode])
            im = im.reduce(factor, box)
            return im.convert(self.mode)

        self.load()

        return self._new(self.im.reduce(factor, box))


    def save(self, fp, format=None, **params):
        """
        Saves this image under the given filename.  If no format is
        specified, the format to use is determined from the filename
        extension, if possible.

        Keyword options can be used to provide additional instructions
        to the writer. If a writer doesn't recognise an option, it is
        silently ignored. The available options are described in the
        :doc:`image format documentation
        <../handbook/image-file-formats>` for each writer.

        You can use a file object instead of a filename. In this case,
        you must always specify the format. The file object must
        implement the ``seek``, ``tell``, and ``write``
        methods, and be opened in binary mode.

        :param fp: A filename (string), pathlib.Path object or file object.
        :param format: Optional format override.  If omitted, the
           format to use is determined from the filename extension.
           If a file object was used instead of a filename, this
           parameter should always be used.
        :param params: Extra parameters to the image writer.
        :returns: None
        :exception ValueError: If the output format could not be determined
           from the file name.  Use the format option to solve this.
        :exception OSError: If the file could not be written.  The file
           may have been created, and may contain partial data.
        """

        filename = ""
        open_fp = False
        if isinstance(fp, Path):
            filename = str(fp)
            open_fp = True
        elif is_path(fp):
            filename = fp
            open_fp = True
        elif fp == sys.stdout:
            try:
                fp = sys.stdout.buffer
            except AttributeError:
                pass
        if not filename and hasattr(fp, "name") and is_path(fp.name):
            # only set the name for metadata purposes
            filename = fp.name

        # may mutate self!
        self._ensure_mutable()

        save_all = params.pop("save_all", False)
        self.encoderinfo = params
        self.encoderconfig = ()

        preinit()

        ext = os.path.splitext(filename)[1].lower()

        if not format:
            if ext not in EXTENSION:
                init()
            try:
                format = EXTENSION[ext]
            except KeyError as e:
                msg = f"unknown file extension: {ext}"
                raise ValueError(msg) from e

        if format.upper() not in SAVE:
            init()
        if save_all:
            save_handler = SAVE_ALL[format.upper()]
        else:
            save_handler = SAVE[format.upper()]

        created = False
        if open_fp:
            created = not os.path.exists(filename)
            if params.get("append", False):
                # Open also for reading ("+"), because TIFF save_all
                # writer needs to go back and edit the written data.
                fp = builtins.open(filename, "r+b")
            else:
                fp = builtins.open(filename, "w+b")

        try:
            save_handler(self, fp, filename)
        except Exception:
            if open_fp:
                fp.close()
            if created:
                try:
                    os.remove(filename)
                except PermissionError:
                    pass
            raise
        if open_fp:
            fp.close()

    def seek(self, frame):
        """
        Seeks to the given frame in this sequence file. If you seek
        beyond the end of the sequence, the method raises an
        ``EOFError`` exception. When a sequence file is opened, the
        library automatically seeks to frame 0.

        See :py:meth:`~PIL.Image.Image.tell`.

        If defined, :attr:`~PIL.Image.Image.n_frames` refers to the
        number of available frames.

        :param frame: Frame number, starting at 0.
        :exception EOFError: If the call attempts to seek beyond the end
            of the sequence.
        """

        # overridden by file handlers
        if frame != 0:
            raise EOFError

    def getchannel(self, channel):
        """
        Returns an image containing a single channel of the source image.

        :param channel: What channel to return. Could be index
          (0 for "R" channel of "RGB") or channel name
          ("A" for alpha channel of "RGBA").
        :returns: An image in "L" mode.

        .. versionadded:: 4.3.0
        """
        self.load()

        if isinstance(channel, str):
            try:
                channel = self.getbands().index(channel)
            except ValueError as e:
                msg = f'The image has no channel "{channel}"'
                raise ValueError(msg) from e

        return self._new(self.im.getband(channel))

    def tell(self):
        """
        Returns the current frame number. See :py:meth:`~PIL.Image.Image.seek`.

        If defined, :attr:`~PIL.Image.Image.n_frames` refers to the
        number of available frames.

        :returns: Frame number, starting with 0.
        """
        return 0

    # FIXME: the different transform methods need further explanation
    # instead of bloating the method docs, add a separate chapter.
    def transform(
        self,
        size,
        method,
        data=None,
        resample=Resampling.NEAREST,
        fill=1,
        fillcolor=None,
    ):
        """
        Transforms this image.  This method creates a new image with the
        given size, and the same mode as the original, and copies data
        to the new image using the given transform.

        :param size: The output size in pixels, as a 2-tuple:
           (width, height).
        :param method: The transformation method.  This is one of
          :py:data:`Transform.EXTENT` (cut out a rectangular subregion),
          :py:data:`Transform.AFFINE` (affine transform),
          :py:data:`Transform.PERSPECTIVE` (perspective transform),
          :py:data:`Transform.QUAD` (map a quadrilateral to a rectangle), or
          :py:data:`Transform.MESH` (map a number of source quadrilaterals
          in one operation).

          It may also be an :py:class:`~PIL.Image.ImageTransformHandler`
          object::

            class Example(Image.ImageTransformHandler):
                def transform(self, size, data, resample, fill=1):
                    # Return result

          It may also be an object with a ``method.getdata`` method
          that returns a tuple supplying new ``method`` and ``data`` values::

            class Example:
                def getdata(self):
                    method = Image.Transform.EXTENT
                    data = (0, 0, 100, 100)
                    return method, data
        :param data: Extra data to the transformation method.
        :param resample: Optional resampling filter.  It can be one of
           :py:data:`Resampling.NEAREST` (use nearest neighbour),
           :py:data:`Resampling.BILINEAR` (linear interpolation in a 2x2
           environment), or :py:data:`Resampling.BICUBIC` (cubic spline
           interpolation in a 4x4 environment). If omitted, or if the image
           has mode "1" or "P", it is set to :py:data:`Resampling.NEAREST`.
           See: :ref:`concept-filters`.
        :param fill: If ``method`` is an
          :py:class:`~PIL.Image.ImageTransformHandler` object, this is one of
          the arguments passed to it. Otherwise, it is unused.
        :param fillcolor: Optional fill color for the area outside the
           transform in the output image.
        :returns: An :py:class:`~PIL.Image.Image` object.
        """

        if self.mode in ("LA", "RGBA") and resample != Resampling.NEAREST:
            return (
                self.convert({"LA": "La", "RGBA": "RGBa"}[self.mode])
                .transform(size, method, data, resample, fill, fillcolor)
                .convert(self.mode)
            )

        if isinstance(method, ImageTransformHandler):
            return method.transform(size, self, resample=resample, fill=fill)

        if hasattr(method, "getdata"):
            # compatibility w. old-style transform objects
            method, data = method.getdata()

        if data is None:
            msg = "missing method data"
            raise ValueError(msg)

        im = new(self.mode, size, fillcolor)
        if self.mode == "P" and self.palette:
            im.palette = self.palette.copy()
        im.info = self.info.copy()
        if method == Transform.MESH:
            # list of quads
            for box, quad in data:
                im.__transformer(
                    box, self, Transform.QUAD, quad, resample, fillcolor is None
                )
        else:
            im.__transformer(
                (0, 0) + size, self, method, data, resample, fillcolor is None
            )

        return im

    def __transformer(
        self, box, image, method, data, resample=Resampling.NEAREST, fill=1
    ):
        w = box[2] - box[0]
        h = box[3] - box[1]

        if method == Transform.AFFINE:
            data = data[:6]

        elif method == Transform.EXTENT:
            # convert extent to an affine transform
            x0, y0, x1, y1 = data
            xs = (x1 - x0) / w
            ys = (y1 - y0) / h
            method = Transform.AFFINE
            data = (xs, 0, x0, 0, ys, y0)

        elif method == Transform.PERSPECTIVE:
            data = data[:8]

        elif method == Transform.QUAD:
            # quadrilateral warp.  data specifies the four corners
            # given as NW, SW, SE, and NE.
            nw = data[:2]
            sw = data[2:4]
            se = data[4:6]
            ne = data[6:8]
            x0, y0 = nw
            As = 1.0 / w
            At = 1.0 / h
            data = (
                x0,
                (ne[0] - x0) * As,
                (sw[0] - x0) * At,
                (se[0] - sw[0] - ne[0] + x0) * As * At,
                y0,
                (ne[1] - y0) * As,
                (sw[1] - y0) * At,
                (se[1] - sw[1] - ne[1] + y0) * As * At,
            )

        else:
            msg = "unknown transformation method"
            raise ValueError(msg)

        if resample not in (
            Resampling.NEAREST,
            Resampling.BILINEAR,
            Resampling.BICUBIC,
        ):
            if resample in (Resampling.BOX, Resampling.HAMMING, Resampling.LANCZOS):
                msg = {
                    Resampling.BOX: "Image.Resampling.BOX",
                    Resampling.HAMMING: "Image.Resampling.HAMMING",
                    Resampling.LANCZOS: "Image.Resampling.LANCZOS",
                }[resample] + f" ({resample}) cannot be used."
            else:
                msg = f"Unknown resampling filter ({resample})."

            filters = [
                f"{filter[1]} ({filter[0]})"
                for filter in (
                    (Resampling.NEAREST, "Image.Resampling.NEAREST"),
                    (Resampling.BILINEAR, "Image.Resampling.BILINEAR"),
                    (Resampling.BICUBIC, "Image.Resampling.BICUBIC"),
                )
            ]
            msg += " Use " + ", ".join(filters[:-1]) + " or " + filters[-1]
            raise ValueError(msg)

        image.load()

        self.load()

        if image.mode in ("1", "P"):
            resample = Resampling.NEAREST

        self.im.transform2(box, image.im, method, data, resample, fill)



# --------------------------------------------------------------------
# Abstract handlers.


class ImagePointHandler:
    """
    Used as a mixin by point transforms
    (for use with :py:meth:`~PIL.Image.Image.point`)
    """

    pass


class ImageTransformHandler:
    """
    Used as a mixin by geometry transforms
    (for use with :py:meth:`~PIL.Image.Image.transform`)
    """

    pass


# --------------------------------------------------------------------
# Factories

#
# Debugging


def _wedge():
    """Create greyscale wedge (for debugging only)"""

    return Image()._new(core.wedge("L"))


def _check_size(size):
    """
    Common check to enforce type and sanity check on size tuples

    :param size: Should be a 2 tuple of (width, height)
    :returns: True, or raises a ValueError
    """

    if not isinstance(size, (list, tuple)):
        msg = "Size must be a tuple"
        raise ValueError(msg)
    if len(size) != 2:
        msg = "Size must be a tuple of length 2"
        raise ValueError(msg)
    if size[0] < 0 or size[1] < 0:
        msg = "Width and height must be >= 0"
        raise ValueError(msg)

    return True


def new(mode, size, color=0):
    """
    Creates a new image with the given mode and size.

    :param mode: The mode to use for the new image. See:
       :ref:`concept-modes`.
    :param size: A 2-tuple, containing (width, height) in pixels.
    :param color: What color to use for the image.  Default is black.
       If given, this should be a single integer or floating point value
       for single-band modes, and a tuple for multi-band modes (one value
       per band).  When creating RGB images, you can also use color
       strings as supported by the ImageColor module.  If the color is
       None, the image is not initialised.
    :returns: An :py:class:`~PIL.Image.Image` object.
    """

    _check_size(size)

    if color is None:
        # don't initialize
        return Image()._new(core.new(mode, size))

    if isinstance(color, str):
        # css3-style specifier

        from . import ImageColor

        color = ImageColor.getcolor(color, mode)

    im = Image()
    if mode == "P" and isinstance(color, (list, tuple)) and len(color) in [3, 4]:
        # RGB or RGBA value for a P image
        from . import ImagePalette

        im.palette = ImagePalette.ImagePalette()
        color = im.palette.getcolor(color)
    return im._new(core.fill(mode, size, color))


def frombytes(mode, size, data, decoder_name="raw", *args):
    """
    Creates a copy of an image memory from pixel data in a buffer.

    In its simplest form, this function takes three arguments
    (mode, size, and unpacked pixel data).

    You can also use any pixel decoder supported by PIL. For more
    information on available decoders, see the section
    :ref:`Writing Your Own File Codec <file-codecs>`.

    Note that this function decodes pixel data only, not entire images.
    If you have an entire image in a string, wrap it in a
    :py:class:`~io.BytesIO` object, and use :py:func:`~PIL.Image.open` to load
    it.

    :param mode: The image mode. See: :ref:`concept-modes`.
    :param size: The image size.
    :param data: A byte buffer containing raw data for the given mode.
    :param decoder_name: What decoder to use.
    :param args: Additional parameters for the given decoder.
    :returns: An :py:class:`~PIL.Image.Image` object.
    """

    _check_size(size)

    # may pass tuple instead of argument list
    if len(args) == 1 and isinstance(args[0], tuple):
        args = args[0]

    if decoder_name == "raw" and args == ():
        args = mode

    im = new(mode, size)
    im.frombytes(data, decoder_name, args)
    return im


def frombuffer(mode, size, data, decoder_name="raw", *args):
    """
    Creates an image memory referencing pixel data in a byte buffer.

    This function is similar to :py:func:`~PIL.Image.frombytes`, but uses data
    in the byte buffer, where possible.  This means that changes to the
    original buffer object are reflected in this image).  Not all modes can
    share memory; supported modes include "L", "RGBX", "RGBA", and "CMYK".

    Note that this function decodes pixel data only, not entire images.
    If you have an entire image file in a string, wrap it in a
    :py:class:`~io.BytesIO` object, and use :py:func:`~PIL.Image.open` to load it.

    In the current version, the default parameters used for the "raw" decoder
    differs from that used for :py:func:`~PIL.Image.frombytes`.  This is a
    bug, and will probably be fixed in a future release.  The current release
    issues a warning if you do this; to disable the warning, you should provide
    the full set of parameters.  See below for details.

    :param mode: The image mode. See: :ref:`concept-modes`.
    :param size: The image size.
    :param data: A bytes or other buffer object containing raw
        data for the given mode.
    :param decoder_name: What decoder to use.
    :param args: Additional parameters for the given decoder.  For the
        default encoder ("raw"), it's recommended that you provide the
        full set of parameters::

            frombuffer(mode, size, data, "raw", mode, 0, 1)

    :returns: An :py:class:`~PIL.Image.Image` object.

    .. versionadded:: 1.1.4
    """

    _check_size(size)

    # may pass tuple instead of argument list
    if len(args) == 1 and isinstance(args[0], tuple):
        args = args[0]

    if decoder_name == "raw":
        if args == ():
            args = mode, 0, 1
        if args[0] in _MAPMODES:
            im = new(mode, (1, 1))
            im = im._new(core.map_buffer(data, size, decoder_name, 0, args))
            if mode == "P":
                from . import ImagePalette

                im.palette = ImagePalette.ImagePalette("RGB", im.im.getpalette("RGB"))
            im.readonly = 1
            return im

    return frombytes(mode, size, data, decoder_name, args)


def fromarray(obj, mode=None):
    """
    Creates an image memory from an object exporting the array interface
    (using the buffer protocol)::

      from PIL import Image
      import numpy as np
      a = np.zeros((5, 5))
      im = Image.fromarray(a)

    If ``obj`` is not contiguous, then the ``tobytes`` method is called
    and :py:func:`~PIL.Image.frombuffer` is used.

    In the case of NumPy, be aware that Pillow modes do not always correspond
    to NumPy dtypes. Pillow modes only offer 1-bit pixels, 8-bit pixels,
    32-bit signed integer pixels, and 32-bit floating point pixels.

    Pillow images can also be converted to arrays::

      from PIL import Image
      import numpy as np
      im = Image.open("hopper.jpg")
      a = np.asarray(im)

    When converting Pillow images to arrays however, only pixel values are
    transferred. This means that P and PA mode images will lose their palette.

    :param obj: Object with array interface
    :param mode: Optional mode to use when reading ``obj``. Will be determined from
      type if ``None``.

      This will not be used to convert the data after reading, but will be used to
      change how the data is read::

        from PIL import Image
        import numpy as np
        a = np.full((1, 1), 300)
        im = Image.fromarray(a, mode="L")
        im.getpixel((0, 0))  # 44
        im = Image.fromarray(a, mode="RGB")
        im.getpixel((0, 0))  # (44, 1, 0)

      See: :ref:`concept-modes` for general information about modes.
    :returns: An image object.

    .. versionadded:: 1.1.6
    """
    arr = obj.__array_interface__
    shape = arr["shape"]
    ndim = len(shape)
    strides = arr.get("strides", None)
    if mode is None:
        try:
            typekey = (1, 1) + shape[2:], arr["typestr"]
        except KeyError as e:
            msg = "Cannot handle this data type"
            raise TypeError(msg) from e
        try:
            mode, rawmode = _fromarray_typemap[typekey]
        except KeyError as e:
            msg = "Cannot handle this data type: %s, %s" % typekey
            raise TypeError(msg) from e
    else:
        rawmode = mode
    if mode in ["1", "L", "I", "P", "F"]:
        ndmax = 2
    elif mode == "RGB":
        ndmax = 3
    else:
        ndmax = 4
    if ndim > ndmax:
        msg = f"Too many dimensions: {ndim} > {ndmax}."
        raise ValueError(msg)

    size = 1 if ndim == 1 else shape[1], shape[0]
    if strides is not None:
        if hasattr(obj, "tobytes"):
            obj = obj.tobytes()
        else:
            obj = obj.tostring()

    return frombuffer(mode, size, obj, "raw", rawmode, 0, 1)


def fromqimage(im):
    """Creates an image instance from a QImage image"""
    from . import ImageQt

    if not ImageQt.qt_is_installed:
        msg = "Qt bindings are not installed"
        raise ImportError(msg)
    return ImageQt.fromqimage(im)


def fromqpixmap(im):
    """Creates an image instance from a QPixmap image"""
    from . import ImageQt

    if not ImageQt.qt_is_installed:
        msg = "Qt bindings are not installed"
        raise ImportError(msg)
    return ImageQt.fromqpixmap(im)


_fromarray_typemap = {
    # (shape, typestr) => mode, rawmode
    # first two members of shape are set to one
    ((1, 1), "|b1"): ("1", "1;8"),
    ((1, 1), "|u1"): ("L", "L"),
    ((1, 1), "|i1"): ("I", "I;8"),
    ((1, 1), "<u2"): ("I", "I;16"),
    ((1, 1), ">u2"): ("I", "I;16B"),
    ((1, 1), "<i2"): ("I", "I;16S"),
    ((1, 1), ">i2"): ("I", "I;16BS"),
    ((1, 1), "<u4"): ("I", "I;32"),
    ((1, 1), ">u4"): ("I", "I;32B"),
    ((1, 1), "<i4"): ("I", "I;32S"),
    ((1, 1), ">i4"): ("I", "I;32BS"),
    ((1, 1), "<f4"): ("F", "F;32F"),
    ((1, 1), ">f4"): ("F", "F;32BF"),
    ((1, 1), "<f8"): ("F", "F;64F"),
    ((1, 1), ">f8"): ("F", "F;64BF"),
    ((1, 1, 2), "|u1"): ("LA", "LA"),
    ((1, 1, 3), "|u1"): ("RGB", "RGB"),
    ((1, 1, 4), "|u1"): ("RGBA", "RGBA"),
    # shortcuts:
    ((1, 1), _ENDIAN + "i4"): ("I", "I"),
    ((1, 1), _ENDIAN + "f4"): ("F", "F"),
}


def _decompression_bomb_check(size):
    if MAX_IMAGE_PIXELS is None:
        return

    pixels = size[0] * size[1]

    if pixels > 2 * MAX_IMAGE_PIXELS:
        msg = (
            f"Image size ({pixels} pixels) exceeds limit of {2 * MAX_IMAGE_PIXELS} "
            "pixels, could be decompression bomb DOS attack."
        )
        raise DecompressionBombError(msg)

    if pixels > MAX_IMAGE_PIXELS:
        warnings.warn(
            f"Image size ({pixels} pixels) exceeds limit of {MAX_IMAGE_PIXELS} pixels, "
            "could be decompression bomb DOS attack.",
            DecompressionBombWarning,
        )


def open(fp, mode="r", formats=None):
    """
    Opens and identifies the given image file.

    This is a lazy operation; this function identifies the file, but
    the file remains open and the actual image data is not read from
    the file until you try to process the data (or call the
    :py:meth:`~PIL.Image.Image.load` method).  See
    :py:func:`~PIL.Image.new`. See :ref:`file-handling`.

    :param fp: A filename (string), pathlib.Path object or a file object.
       The file object must implement ``file.read``,
       ``file.seek``, and ``file.tell`` methods,
       and be opened in binary mode.
    :param mode: The mode.  If given, this argument must be "r".
    :param formats: A list or tuple of formats to attempt to load the file in.
       This can be used to restrict the set of formats checked.
       Pass ``None`` to try all supported formats. You can print the set of
       available formats by running ``python3 -m PIL`` or using
       the :py:func:`PIL.features.pilinfo` function.
    :returns: An :py:class:`~PIL.Image.Image` object.
    :exception FileNotFoundError: If the file cannot be found.
    :exception PIL.UnidentifiedImageError: If the image cannot be opened and
       identified.
    :exception ValueError: If the ``mode`` is not "r", or if a ``StringIO``
       instance is used for ``fp``.
    :exception TypeError: If ``formats`` is not ``None``, a list or a tuple.
    """

    if mode != "r":
        msg = f"bad mode {repr(mode)}"
        raise ValueError(msg)
    elif isinstance(fp, io.StringIO):
        msg = (
            "StringIO cannot be used to open an image. "
            "Binary data must be used instead."
        )
        raise ValueError(msg)

    if formats is None:
        formats = ID
    elif not isinstance(formats, (list, tuple)):
        msg = "formats must be a list or tuple"
        raise TypeError(msg)

    exclusive_fp = False
    filename = ""
    if isinstance(fp, Path):
        filename = str(fp.resolve())
    elif is_path(fp):
        filename = fp

    if filename:
        fp = builtins.open(filename, "rb")
        exclusive_fp = True

    try:
        fp.seek(0)
    except (AttributeError, io.UnsupportedOperation):
        fp = io.BytesIO(fp.read())
        exclusive_fp = True

    prefix = fp.read(16)

    preinit()

    accept_warnings = []

    def _open_core(fp, filename, prefix, formats):
        for i in formats:
            i = i.upper()
            if i not in OPEN:
                init()
            try:
                factory, accept = OPEN[i]
                result = not accept or accept(prefix)
                if type(result) in [str, bytes]:
                    accept_warnings.append(result)
                elif result:
                    fp.seek(0)
                    im = factory(fp, filename)
                    _decompression_bomb_check(im.size)
                    return im
            except (SyntaxError, IndexError, TypeError, struct.error):
                # Leave disabled by default, spams the logs with image
                # opening failures that are entirely expected.
                # logger.debug("", exc_info=True)
                continue
            except BaseException:
                if exclusive_fp:
                    fp.close()
                raise
        return None

    im = _open_core(fp, filename, prefix, formats)

    if im is None and formats is ID:
        checked_formats = formats.copy()
        if init():
            im = _open_core(
                fp,
                filename,
                prefix,
                tuple(format for format in formats if format not in checked_formats),
            )

    if im:
        im._exclusive_fp = exclusive_fp
        return im

    if exclusive_fp:
        fp.close()
    for message in accept_warnings:
        warnings.warn(message)
    msg = "cannot identify image file %r" % (filename if filename else fp)
    raise UnidentifiedImageError(msg)


#
# Image processing.


def merge(mode, bands):
    """
    Merge a set of single band images into a new multiband image.

    :param mode: The mode to use for the output image. See:
        :ref:`concept-modes`.
    :param bands: A sequence containing one single-band image for
        each band in the output image.  All bands must have the
        same size.
    :returns: An :py:class:`~PIL.Image.Image` object.
    """

    if getmodebands(mode) != len(bands) or "*" in mode:
        msg = "wrong number of bands"
        raise ValueError(msg)
    for band in bands[1:]:
        if band.mode != getmodetype(mode):
            msg = "mode mismatch"
            raise ValueError(msg)
        if band.size != bands[0].size:
            msg = "size mismatch"
            raise ValueError(msg)
    for band in bands:
        band.load()
    return bands[0]._new(core.merge(mode, *[b.im for b in bands]))


# --------------------------------------------------------------------
# Plugin registry


def register_open(id, factory, accept=None):
    """
    Register an image file plugin.  This function should not be used
    in application code.

    :param id: An image format identifier.
    :param factory: An image file factory method.
    :param accept: An optional function that can be used to quickly
       reject images having another format.
    """
    id = id.upper()
    if id not in ID:
        ID.append(id)
    OPEN[id] = factory, accept


def register_mime(id, mimetype):
    """
    Registers an image MIME type.  This function should not be used
    in application code.

    :param id: An image format identifier.
    :param mimetype: The image MIME type for this format.
    """
    MIME[id.upper()] = mimetype


def register_save(id, driver):
    """
    Registers an image save function.  This function should not be
    used in application code.

    :param id: An image format identifier.
    :param driver: A function to save images in this format.
    """
    SAVE[id.upper()] = driver


def register_save_all(id, driver):
    """
    Registers an image function to save all the frames
    of a multiframe format.  This function should not be
    used in application code.

    :param id: An image format identifier.
    :param driver: A function to save images in this format.
    """
    SAVE_ALL[id.upper()] = driver


def register_extension(id, extension):
    """
    Registers an image extension.  This function should not be
    used in application code.

    :param id: An image format identifier.
    :param extension: An extension used for this format.
    """
    EXTENSION[extension.lower()] = id.upper()


def register_extensions(id, extensions):
    """
    Registers image extensions.  This function should not be
    used in application code.

    :param id: An image format identifier.
    :param extensions: A list of extensions used for this format.
    """
    for extension in extensions:
        register_extension(id, extension)


def registered_extensions():
    """
    Returns a dictionary containing all file extensions belonging
    to registered plugins
    """
    init()
    return EXTENSION


def register_decoder(name, decoder):
    """
    Registers an image decoder.  This function should not be
    used in application code.

    :param name: The name of the decoder
    :param decoder: A callable(mode, args) that returns an
                    ImageFile.PyDecoder object

    .. versionadded:: 4.1.0
    """
    DECODERS[name] = decoder


def register_encoder(name, encoder):
    """
    Registers an image encoder.  This function should not be
    used in application code.

    :param name: The name of the encoder
    :param encoder: A callable(mode, args) that returns an
                    ImageFile.PyEncoder object

    .. versionadded:: 4.1.0
    """
    ENCODERS[name] = encoder


# --------------------------------------------------------------------
# Simple display support.


def _show(image, **options):
    from . import ImageShow

    ImageShow.show(image, **options)

