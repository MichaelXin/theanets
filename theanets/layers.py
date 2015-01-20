# Copyright (c) 2012-2014 Leif Johnson <leif@leifjohnson.net>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

'''This module contains classes for different types of network layers.'''

import climate
import functools
import numpy as np
import sys
import theano
import theano.tensor as TT

from theano.sandbox.rng_mrg import MRG_RandomStreams as RandomStreams

logging = climate.get_logger(__name__)

FLOAT = theano.config.floatX


def create_matrix(nin, nout, name, sparsity=0, radius=0, mean=0, std=None):
    '''Create a matrix of randomly-initialized weights.

    Parameters
    ----------
    nin : int
        Number of rows of the weight matrix -- equivalently, the number of
        "input" units that the weight matrix connects.
    nout : int
        Number of columns of the weight matrix -- equivalently, the number
        of "output" units that the weight matrix connects.
    name : str
        A string to use as the theano name for the created variable.
    sparsity : float in (0, 1), optional
        If given, ensure that the given fraction of the weight matrix is
        set to zero. Defaults to 0, meaning all weights are nonzero.
    radius : float, optional
        If given, rescale the initial weights to have this spectral radius.
        No scaling is performed by default.
    mean : float, optional
        Draw initial weight values from a normal with this mean. Defaults to 0.
    std : float, optional
        Draw initial weight values from a normal with this standard deviation.
        Defaults to :math:`1 / \sqrt{n_i + n_o}`.

    Returns
    -------
    matrix : theano shared array
        A shared array containing a matrix of theano values. These often
        represent the weights connecting each "input" unit to each "output" unit
        in a layer.
    '''
    std = std or 1 / np.sqrt(nin + nout)
    arr = mean + std * np.random.randn(nin, nout)
    if 1 > sparsity > 0:
        k = min(nin, nout)
        mask = np.random.binomial(n=1, p=1 - sparsity, size=(nin, nout)).astype(bool)
        mask[:k, :k] |= np.random.permutation(np.eye(k).astype(bool))
        arr *= mask
    if radius > 0:
        # rescale weights to have the appropriate spectral radius.
        u, s, vT = np.linalg.svd(arr)
        arr = np.dot(np.dot(u, np.diag(radius * s / abs(s[0]))), vT)
    return theano.shared(arr.astype(FLOAT), name=name)


def create_vector(size, name, mean=0, std=1e-3):
    '''Create a vector of small values.

    Parameters
    ----------
    size : int
        Length of vecctor to create.
    name : str
        A string to use as the theano name for the created variables.
    mean : float, optional
        Mean value for initial vector values. Defaults to 0.
    std : float, optional
        Standard deviation for initial vector values. Defaults to 1e-6.

    Returns
    -------
    vector : theano shared array
        A shared array containing a vector of theano values. This often
        represents the bias for a layer of computation units.
    '''
    vec = mean + std * np.random.randn(size)
    return theano.shared(vec.astype(FLOAT), name=name)


def create_activation(activation):

    '''Given an activation description, return a callable that implements it.

    Parameters
    ----------
    activation : string
        A string description of an activation function to use.

    Returns
    -------
    activation : callable(float) -> float
        A callable activation function.
    '''
    def compose(a, b):
        c = lambda z: b(a(z))
        c.__theanets_name__ = '%s(%s)' % (b.__theanets_name__, a.__theanets_name__)
        return c
    if '+' in activation:
        return functools.reduce(
            compose, (create_activation(a) for a in activation.split('+')))
    options = {
        'tanh': TT.tanh,
        'linear': lambda z: z,
        'logistic': TT.nnet.sigmoid,
        'sigmoid': TT.nnet.sigmoid,
        'softplus': TT.nnet.softplus,
        'softmax': TT.nnet.softmax,

        # rectification
        'relu': lambda z: TT.maximum(0, z),
        'trel': lambda z: TT.maximum(0, TT.minimum(1, z)),
        'trec': lambda z: TT.maximum(1, z),
        'tlin': lambda z: z * (abs(z) > 1),

        # modifiers
        'rect:max': lambda z: TT.minimum(1, z),
        'rect:min': lambda z: TT.maximum(0, z),

        # normalization
        'norm:dc': lambda z: z - z.mean(axis=-1, keepdims=True),
        'norm:max': lambda z: z / TT.maximum(1e-7, abs(z).max(axis=-1, keepdims=True)),
        'norm:std': lambda z: z / TT.maximum(1e-7, TT.std(z, axis=-1, keepdims=True)),
        'norm:z': lambda z: (z - z.mean(axis=-1, keepdims=True)) / TT.maximum(1e-7, z.std(axis=-1, keepdims=True)),
        }
    for k, v in options.items():
        v.__theanets_name__ = k
    try:
        return options[activation.lower()]
    except KeyError:
        raise KeyError('unknown activation {}'.format(activation))


def add_noise(input, level, rng):
    '''Add noise to elements of the input variable as needed.

    Parameters
    ----------
    input : theano variable
        Input variable to add noise to.
    level : float
        Standard deviation of gaussian noise to add to the input. If this is
        0, then no gaussian noise is added to the input.

    Returns
    -------
    output : theano variable
        The input variable, plus additional noise as specified.
    '''
    if level == 0:
        return input
    return input + rng.normal(size=input.shape, std=level, dtype=FLOAT)


def add_dropout(input, probability, rng):
    '''Add dropouts to elements of the input variable as needed.

    Parameters
    ----------
    input : theano variable
        Input variable to add dropouts to.
    probability : float, in [0, 1]
        Probability of dropout for each element of the input. If this is 0,
        then no elements of the input are set randomly to 0.

    Returns
    -------
    output : theano variable
        The input variable, plus additional dropouts as specified.
    '''
    if probability == 0:
        return input
    return input * rng.binomial(size=input.shape, n=1, p=1-probability, dtype=FLOAT)


def build(layer, *args, **kwargs):
    '''Construct a layer by name.

    Parameters
    ----------
    layer : str
        The name of the type of layer to build.
    args : tuple
        Positional arguments to pass to the layer constructor.
    kwargs : dict
        Named arguments to pass to the layer constructor.

    Returns
    -------
    layer : :class:`Layer`
        A neural network layer instance.
    '''
    return Layer.build(layer, *args, **kwargs)


class Registrar(type):
    '''A metaclass that builds a registry of its subclasses.'''

    def __init__(cls, name, bases, dct):
        if not hasattr(cls, '_registry'):
            cls._registry = {}
        else:
            cls._registry[name.lower()] = cls
        super(Registrar, cls).__init__(name, bases, dct)

    def build(cls, key, *args, **kwargs):
        return cls._registry[key.lower()](*args, **kwargs)

Base = Registrar(str('Base'), (), {})


def _only(x):
    '''Normalize the type of x to return one element.

    Parameters
    ----------
    x : any
        Either a sequence of elements containing one value, or a non-sequence.

    Raises
    ------
    AssertionError :
        If x is a sequence such that len(x) != 1.

    Returns
    -------
    element : any
        If x is a sequence, returns the first element from the sequence. If x is
        not a sequence, returns x.
    '''
    if hasattr(x, '__len__'):
        assert len(x) == 1
        return x[0]
    return x


class Layer(Base):
    '''Layers in network graphs derive from this base class.

    In ``theanets``, a layer refers to a set of weights and biases, plus the
    "output" units that produce some sort of signal for further layers to
    consume. The first layer in a network, the input layer, is a special case
    with linear activation and no weights or bias.

    Parameters
    ----------
    nin : int or tuple of int
        Size of input vector(s) to this layer.
    nout : int
        Size of output vector produced by this layer.
    name : str, optional
        The name of this layer. If not given, layers will be numbered
        sequentially based on the order in which they are created.
    rng : random number generator, optional
        A theano random number generator to use for creating noise and dropout
        values. If not provided, a new generator will be produced for this
        layer.
    noise : positive float, optional
        Add isotropic gaussian noise with the given standard deviation to the
        output of this layer. Defaults to 0, which does not add any noise to the
        output.
    dropout : float in (0, 1), optional
        Set the given fraction of outputs in this layer randomly to zero.
        Defaults to 0, which does not drop out any units.
    activation : str, optional
        The name of an activation function to use for units in this layer. See
        :func:`build_activation`.
    sparsity : float in (0, 1), optional
        If given, create sparse connections in the layer's weight matrix, such
        that this fraction of the weights is set to zero. By default, this
        parameter is 0, meaning all weights are nonzero.

    Attributes
    ----------
    kwargs : dict
        Keyword arguments that were used when constructing this layer.
    activate : callable
        The activation function to use on this layer's output units.
    weights : list of theano shared variables
        A list of weight matrix(ces) for this layer.
    biases : list of theano shared variables
        A list of bias vector(s) for this layer.
    '''

    count = 0

    def __init__(self, **kwargs):
        Layer.count += 1
        self.kwargs = kwargs
        self.name = kwargs.get('name', 'layer{}'.format(Layer.count))
        self.nin = kwargs['nin']
        self.nout = kwargs['nout']
        self.activate = create_activation(kwargs.get('activation', 'logistic'))
        self.weights = []
        self.biases = []
        super(Layer, self).__init__()

    def output(self, inputs):
        '''Create theano variables representing the output of this layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            Symbolic inputs to this layer. Usually layers have only one input,
            but layers in general are allowed to have many inputs.

        Returns
        -------
        output : theano expression
            Theano expression specifying the output of this layer.
        updates : sequence of update tuples
            Updates that should be performed by a theano function that computes
            something using this layer.
        '''
        rng = self.kwargs.get('rng') or RandomStreams()
        noise = self.kwargs.get('noise', 0)
        dropout = self.kwargs.get('dropout', 0)
        output, updates = self.transform(inputs)
        return add_dropout(add_noise(output, noise, rng), dropout, rng), updates

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            Symbolic inputs to this layer. There must be exactly one input.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from this layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        return _only(inputs), ()

    def reset(self):
        '''Reset the state of this layer to a new initial condition.

        Returns
        -------
        count : int
            A count of the number of parameters in this layer.
        '''
        return 0

    def get_params(self, exclude_bias=False):
        '''Get a list of parameters in this layer that can be optimized.

        Parameters
        ----------
        exclude_bias : bool, optional
            If True, then do not include bias parameters in this list. Defaults
            to False, which includes both weights and bias parameters.

        Returns
        -------
        params : list of theano shared variables
            A list of the parameters in this layer that can be optimized.
        '''
        return self.weights if exclude_bias else self.weights + self.biases

    def get_values(self):
        '''Get the values of the parameters in this layer.

        Returns
        -------
        values : list of ndarray
            A list of numpy arrays, one for each parameter in the layer.
        '''
        return [p.get_value() for p in self.get_params()]

    def set_values(self, values):
        '''Set the parameters in this layer to a set of values.

        Parameters
        ----------
        values : list of ndarray
            A list of numpy arrays to set each parameter to. This must contain
            the same number of elements as the layer has parameters to set.
        '''
        for v, p in zip(values, self.get_params()):
            p.set_value(v)

    def _fmt(self, string):
        '''Helper method to format our name into a string.'''
        if '{' not in string:
            string = '{}_' + string
        return string.format(self.name)

    def _new_weights(self, nin=None, nout=None, name='weights'):
        '''Helper method to create a new weight matrix.

        Parameters
        ----------
        nin : int, optional
            Size of "input" for this weight matrix. Defaults to self.nin.
        nout : int, optional
            Size of "output" for this weight matrix. Defaults to self.nout.
        name : str, optional
            Name of theano shared variable. Defaults to self.name + "_weights".

        Returns
        -------
        matrix : theano shared variable
            A shared variable containing a newly initialized weight matrix.
        '''
        return create_matrix(
            nin or self.nin,
            nout or self.nout,
            name=self._fmt(name),
            sparsity=self.kwargs.get('sparsity', 0))

    def _new_bias(self, name='bias', mean=0, std=1e-3):
        '''Helper method to create a new bias vector.

        Parameters
        ----------
        name : str, optional
            Name of theano shared variable. Defaults to self.name + "_bias".
        mean : float, optional
            Mean value for randomly-initialized biases. Defaults to 0.
        std : float, optional
            Standard deviation for randomly-initialized biases. Defaults to 1e-3.

        Returns
        -------
        vector : theano shared variable
            A shared variable containing a newly initialized bias vector.
        '''
        return create_vector(self.nout, self._fmt(name), mean=mean, std=std)


class Input(Layer):
    '''The input of a network is a special type of layer with no parameters.

    Input layers essentially add only noise to the input data (if desired), but
    otherwise reproduce their inputs exactly.
    '''

    def __init__(self, size, **kwargs):
        kwargs['nin'] = 0
        kwargs['nout'] = size
        kwargs['activation'] = 'linear'
        super(Input, self).__init__(**kwargs)


class Feedforward(Layer):
    '''A feedforward neural network layer performs a transform of its input.

    More precisely, feedforward layers as implemented here perform a weighted
    (affine) transformation of their input, followed by a potentially nonlinear
    "activation" function performed on the transformed input. Feedforward layers
    are the fundamental building block on which most neural network models are
    built.
    '''

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            Symbolic inputs to this layer.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from this layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        if not hasattr(inputs, '__len__'):
            inputs = (inputs, )
        assert len(inputs) == len(self.weights)
        xs = (TT.dot(i, w) for i, w in zip(inputs, self.weights))
        return self.activate(sum(xs) + self.biases[0]), ()

    def reset(self):
        '''Reset the state of this layer to a new initial condition.

        Returns
        -------
        count : int
            A count of the number of parameters in this layer.
        '''
        logging.info('initializing %s: %s x %s', self.name, self.nin, self.nout)
        nins = self.nin
        if isinstance(nins, int):
            nins = (nins, )
        self.weights = [self._new_weights(nin=n, name='weights_{}'.format(n)) for n in nins]
        self.biases = [self._new_bias()]
        return self.nout * (sum(nins) + 1)


class Tied(Feedforward):
    '''A tied-weights feedforward layer shadows weights from another layer.

    Tied weights are typically featured in some types of autoencoder models
    (e.g., PCA). A layer with tied weights requires a "partner" layer -- the
    tied layer borrows the weights from its partner and uses the transpose of
    them to perform its feedforward mapping. Thus, tied layers do not have their
    own weights. On the other hand, tied layers do have their own bias values,
    but these can be fixed to zero during learning to simulate networks with no
    bias (e.g., PCA on mean-centered data).

    Attributes
    ----------
    partner : :class:`Layer`
        The "partner" layer to which this layer is tied.
    '''

    def __init__(self, partner, **kwargs):
        self.partner = partner
        kwargs['nin'] = partner.nout
        kwargs['nout'] = partner.nin
        super(Tied, self).__init__(**kwargs)

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            Symbolic inputs to this layer. There must be exactly one input.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from this layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        x = TT.dot(_only(inputs), self.partner.weights[0].T) + self.biases[0]
        return self.activate(x), ()

    def reset(self):
        '''Reset the state of this layer to a new initial condition.

        Returns
        -------
        count : int
            A count of the number of parameters in this layer.
        '''
        logging.info('tied weights from %s: %s x %s',
                     self.partner.name, self.nin, self.nout)
        # this layer does not create a weight matrix!
        self.biases = [self._new_bias()]
        return self.nout


class Classifier(Feedforward):
    '''A classifier layer performs a softmax over a linear input transform.

    Classifier layers are typically the "output" layer of a classifier network.
    '''

    def __init__(self, **kwargs):
        kwargs['activation'] = 'softmax'
        super(Classifier, self).__init__(**kwargs)


class Recurrent(Layer):
    '''A recurrent network layer incorporates some dependency on past values.

    In many respects, a recurrent network layer is much like a basic feedforward
    layer: both layers take an input signal, apply some transformation to it,
    and produce an output signal. Recurrent layers, however, additionally
    preserve the previous state(s) of the layer's output and incorporate them
    into the transformation of the current input.

    This layer type is actually just a base class for the many different types
    of recurrent network layers, for example :class:`RNN` or :class:`LSTM`.

    Parameters
    ----------
    radius : float, optional
        If given, rescale the initial weights for the recurrent units to have
        this spectral radius. No scaling is performed by default.

    direction : {None, 'back', 'backwards'}, optional
        If given, this string indicates whether the recurrency for this layer
        should run "backwards", with future states influencing the current
        state. The default is None, which runs the recurrency forwards in time
        so that past states influence the current state of the layer.
    '''

    def __init__(self, batch_size=64, **kwargs):
        super(Recurrent, self).__init__(**kwargs)

        zeros = np.zeros((batch_size, self.nout), FLOAT)
        self.zeros = lambda s='h': theano.shared(zeros, name=self._fmt('{}0'.format(s)))

    def _new_weights(self, nin=None, nout=None, name='weights'):
        '''Helper method to create a new weight matrix.

        Parameters
        ----------
        nin : int, optional
            Size of "input" for this weight matrix. Defaults to self.nin.
        nout : int, optional
            Size of "output" for this weight matrix. Defaults to self.nout.
        name : str, optional
            Name of theano shared variable. Defaults to self.name + "_weights".

        Returns
        -------
        matrix : theano shared variable
            A shared variable containing a newly initialized weight matrix.
        '''
        nin = nin or self.nin
        nout = nout or self.nout
        return create_matrix(
            nin,
            nout,
            name=self._fmt(name),
            radius=self.kwargs.get('radius', 0) if nin == nout else 0,
            sparsity=self.kwargs.get('sparsity', 0))

    def _scan(self, name, fn, inputs, inits=None):
        '''Helper method for defining a basic loop in theano.

        Parameters
        ----------
        name : str
            Name of the scan variable to create.
        fn : callable
            The callable to apply in the loop.
        inputs : sequence of theano expressions
            Inputs to the scan operation.

        Returns
        -------
        output(s) : sequence of theano expression(s)
            Theano expression(s) representing output(s) from the scan.
        updates : list of theano variables
            A sequence of updates to apply inside a theano function.
        '''
        if self.kwargs.get('direction', '').lower().startswith('back'):
            inputs = [x[::-1] for x in inputs]
        if inits is None:
            inits = [self.zeros()]
        return theano.scan(fn, name=name, sequences=inputs, outputs_info=inits)


class RNN(Recurrent):
    '''"Vanilla" recurrent network layer.

    There are many different styles of recurrent network layers, but the one
    implemented here is known as an Elman layer or an SRN (Simple Recurrent
    Network) -- the output from the layer at the previous time step is
    incorporated into the input of the layer at the current time step.
    '''

    def reset(self):
        '''Reset the state of this layer to a new initial condition.

        Returns
        -------
        count : int
            The number of learnable parameters in this layer.
        '''
        logging.info('initializing %s: %s x %s', self.name, self.nin, self.nout)
        self.weights = [self._new_weights(name='xh'),
                        self._new_weights(nin=self.nout, name='hh')]
        self.biases = [self._new_bias()]
        return self.nout * (1 + self.nin + self.nout)

    _W_xh = property(lambda self: self.weights[0])
    _W_hh = property(lambda self: self.weights[1])
    _b_h = property(lambda self: self.biases[0])

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            The inputs to this layer. There must be exactly one input.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from the layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        def fn(x_t, h_tm1):
            return self.activate(x_t + TT.dot(h_tm1, self._W_hh))
        x = TT.dot(_only(inputs), self._W_xh) + self._b_h
        return self._scan(self._fmt('rnn'), fn, [x])


class ARRNN(Recurrent):
    '''An adaptive rate RNN defines per-hidden-unit accumulation rates.

    In a normal RNN, a hidden unit is updated completely at each time step,
    :math:`h_t = f(x_t, h_{t-1})`. With an explicit update rate, the state of a
    hidden unit is computed as a mixture of the new and old values, `h_t =
    \alpha_t h_{t-1} + (1 - \alpha_t) f(x_t, h_{t-1})`.

    Rates might be defined in a number of ways: as a vector of values sampled
    randomly from (0, 1), or even as a learnable vector of values. But in the
    adaptive rate RNN, the rate values are computed at each time step as a
    logistic sigmoid applied to an affine transform of the input:
    :math:`\alpha_t = 1 / (1 + e^{-x_t W_{xr} - b_r})`.
    '''

    def reset(self):
        '''Reset the state of this layer to a new initial condition.

        Returns
        -------
        count : int
            The number of learnable parameters in this layer.
        '''
        logging.info('initializing %s: %s x %s', self.name, self.nin, self.nout)
        self.weights = [
            self._new_weights(name='xh'),
            self._new_weights(name='xr'),
            self._new_weights(nin=self.nout, name='hh'),
        ]
        self.biases = [self._new_bias('hid'), self._new_bias('rate')]
        return self.nout * (2 + 2 * self.nin + self.nout)

    _W_xh = property(lambda self: self.weights[0])
    _W_xr = property(lambda self: self.weights[1])
    _W_hh = property(lambda self: self.weights[2])
    _b_h = property(lambda self: self.biases[0])
    _b_r = property(lambda self: self.biases[1])

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            The inputs to this layer. There must be exactly one input.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from the layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        def fn(x_t, r_t, h_tm1):
            h_t = self.activate(x_t + TT.dot(h_tm1, self._W_hh))
            return r_t * h_tm1 + (1 - r_t) * h_t
        x = _only(inputs)
        h = TT.dot(x, self._W_xh) + self._b_h
        r = TT.dot(x, self._W_xr) + self._b_r
        return self._scan(self._fmt('arrnn'), fn, [h, TT.nnet.sigmoid(r)])


class MRNN(Recurrent):
    '''Define a recurrent network layer using multiplicative dynamics.

    The formulation of MRNN implemented here uses a factored dynamics matrix as
    described in Sutskever, Martens & Hinton, ICML 2011, "Generating text with
    recurrent neural networks." This paper is available online at
    http://www.icml-2011.org/papers/524_icmlpaper.pdf.
    '''

    def __init__(self, factors=None, **kwargs):
        self.factors = factors or int(np.ceil(np.sqrt(kwargs['nout'])))
        super(MRNN, self).__init__(**kwargs)

    def reset(self):
        '''Reset the weights and biases for this layer to random values.

        Returns
        -------
        count : int
            The number of learnable parameters in this layer.
        '''
        logging.info('initializing %s: %s x %s', self.name, self.nin, self.nout)
        self.weights = [
            self._new_weights(self.nin, self.nout, 'xh'),
            self._new_weights(self.nin, self.factors, 'xf'),
            self._new_weights(self.nout, self.factors, 'hf'),
            self._new_weights(self.factors, self.nout, 'fh'),
        ]
        self.biases = [self._new_bias()]
        return self.nout * (1 + self.nin) + self.factors * (2 * self.nout + self.nin)

    _W_xh = property(lambda self: self.weights[0])
    _W_xf = property(lambda self: self.weights[1])
    _W_hf = property(lambda self: self.weights[2])
    _W_fh = property(lambda self: self.weights[3])
    _b_h = property(lambda self: self.biases[0])

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            The inputs to this layer. There must be exactly one input.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from the layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        def fn(x_t, f_t, h_tm1):
            h_t = TT.dot(f_t * TT.dot(h_tm1, self._W_hf), self._W_fh)
            return self.activate(x_t + h_t)
        x = _only(inputs)
        h = TT.dot(x, self._W_xh) + self._b_h
        f = TT.dot(x, self._W_xf)
        return self._scan(self._fmt('mrnn'), fn, [h, f])


class LSTM(Recurrent):
    '''Long Short-Term Memory layer.

    The implementation details for this layer follow the specification given by
    A. Graves, "Generating Sequences with Recurrent Neural Networks,"
    http://arxiv.org/pdf/1308.0850v5.pdf (page 5).
    '''

    def reset(self):
        '''Reset the weights and biases for this layer to random values.

        Returns
        -------
        count : int
            The number of learnable parameters in this layer.
        '''
        logging.info('initializing %s: %s x %s', self.name, self.nin, self.nout)
        self.weights = [
            # these three "peephole" weight matrices are always diagonal.
            self._new_bias(name='ci'),
            self._new_bias(name='cf'),
            self._new_bias(name='co'),

            self._new_weights(name='xi'),
            self._new_weights(name='xf'),
            self._new_weights(name='xc'),
            self._new_weights(name='xo'),

            self._new_weights(nin=self.nout, name='hi'),
            self._new_weights(nin=self.nout, name='hf'),
            self._new_weights(nin=self.nout, name='hc'),
            self._new_weights(nin=self.nout, name='ho'),
        ]
        self.biases = [
            self._new_bias(name='bi'),
            self._new_bias(name='bf', mean=10),
            self._new_bias(name='bc'),
            self._new_bias(name='bo'),
        ]
        return self.nout * (7 + 4 * (self.nout + self.nin))

    _W_ci = property(lambda self: self.weights[0])
    _W_cf = property(lambda self: self.weights[1])
    _W_co = property(lambda self: self.weights[2])
    _W_xi = property(lambda self: self.weights[3])
    _W_xf = property(lambda self: self.weights[4])
    _W_xc = property(lambda self: self.weights[5])
    _W_xo = property(lambda self: self.weights[6])
    _W_hi = property(lambda self: self.weights[7])
    _W_hf = property(lambda self: self.weights[8])
    _W_hc = property(lambda self: self.weights[9])
    _W_ho = property(lambda self: self.weights[10])
    _b_i = property(lambda self: self.biases[0])
    _b_f = property(lambda self: self.biases[1])
    _b_c = property(lambda self: self.biases[2])
    _b_o = property(lambda self: self.biases[3])

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            The inputs to this layer. There must be exactly one input.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from the layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        def fn(xi, xf, xc, xo, h_tm1, c_tm1):
            i_t = TT.nnet.sigmoid(xi + TT.dot(h_tm1, self._W_hi) + c_tm1 * self._W_ci)
            f_t = TT.nnet.sigmoid(xf + TT.dot(h_tm1, self._W_hf) + c_tm1 * self._W_cf)
            c_t = f_t * c_tm1 + i_t * TT.tanh(xc + TT.dot(h_tm1, self._W_hc))
            o_t = TT.nnet.sigmoid(xo + TT.dot(h_tm1, self._W_ho) + c_t * self._W_co)
            h_t = o_t * TT.tanh(c_t)
            return h_t, c_t
        x = _only(inputs)
        (output, _), updates = self._scan(
            self._fmt('lstm'), fn,
            [TT.dot(x, self._W_xi) + self._b_i,
             TT.dot(x, self._W_xf) + self._b_f,
             TT.dot(x, self._W_xc) + self._b_c,
             TT.dot(x, self._W_xo) + self._b_o],
            inits=[self.zeros('h'), self.zeros('c')])
        return output, updates


class Bidirectional(Layer):
    '''A bidirectional recurrent layer runs worker models forward and backward.

    The outputs of the forward and backward passes are combined using an affine
    transformation into the overall output for the layer.

    For an example specification of a bidirectional recurrent network, see A.
    Graves, N. Jaitly, and A. Mohamed, "Hybrid Speech Recognition with Deep
    Bidirectional LSTM," 2013. http://www.cs.toronto.edu/~graves/asru_2013.pdf

    Parameters
    ----------
    worker : str, optional
        This string specifies the type of worker layer to use for the forward
        and backward processing. This parameter defaults to 'rnn' (i.e., vanilla
        recurrent network layer), but can be given as any string that specifies
        a recurrent layer type.
    '''

    def __init__(self, worker='rnn', **kwargs):
        if 'direction' in kwargs:
            kwargs.pop('direction')
        super(Bidirectional, self).__init__(**kwargs)
        if 'name' in kwargs:
            kwargs.pop('name')
        self.forward = build(worker, direction='forward', name=self._fmt('fw'), **kwargs)
        self.backward = build(worker, direction='backward', name=self._fmt('bw'), **kwargs)

    def reset(self):
        '''Reset the weights and biases for this layer to random values.

        Returns
        -------
        count : int
            The number of learnable parameters in this layer.
        '''
        logging.info('initializing %s: %s<-> x %s', self.name, self.forward.nin, self.nout)
        nf = self.forward.reset()
        # we "tie" together the weights for the forward and backward RNNs.
        self.backward.weights = self.forward.weights
        self.backward.biases = self.forward.biases
        self.fw = self._new_weights(nin=self.forward.nout, name='fw')
        self.bw = self._new_weights(nin=self.forward.nout, name='bw')
        self.ob = self._new_bias()
        self.weights = self.forward.weights + [self.fw, self.bw]
        self.biases = self.forward.biases + [self.ob]
        return nf + self.nout * (1 + 2 * self.forward.nout)

    def transform(self, inputs):
        '''Transform the inputs for this layer into an output for the layer.

        Parameters
        ----------
        inputs : sequence of theano expressions
            The inputs to this layer. There must be exactly one input.

        Returns
        -------
        output : theano expression
            Theano expression representing the output from the layer.
        updates : sequence of update tuples
            A sequence of updates to apply inside a theano function.
        '''
        fx, fu = self.forward.transform(inputs)
        bx, bu = self.backward.transform(inputs)
        return TT.dot(fx, self.fw) + TT.dot(bx, self.bw) + self.ob, fu + bu
