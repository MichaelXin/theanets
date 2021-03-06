import theanets
import numpy as np

import util


class TestNetwork(util.MNIST):
    def _build(self, *hiddens, **kwargs):
        return theanets.Regressor(
            layers=(self.DIGIT_SIZE, ) + hiddens,
            hidden_activation='logistic',
            **kwargs)

    def test_predict(self):
        net = self._build(15, 13)
        y = net.predict(self.images)
        assert y.shape == (self.NUM_DIGITS, 13)

    def test_feed_forward(self):
        net = self._build(15, 13)
        hs = net.feed_forward(self.images)
        assert len(hs) == 3
        assert hs[0].shape == (self.NUM_DIGITS, self.DIGIT_SIZE)
        assert hs[1].shape == (self.NUM_DIGITS, 15)
        assert hs[2].shape == (self.NUM_DIGITS, 13)

    def test_decode_from(self):
        net = self._build(13, 14, 15, decode_from=2)
        hs = net.feed_forward(self.images)
        assert len(hs) == 4
        assert hs[0].shape == (self.NUM_DIGITS, self.DIGIT_SIZE)
        assert hs[1].shape == (self.NUM_DIGITS, 13)
        assert hs[2].shape == (self.NUM_DIGITS, 14)
        assert hs[3].shape == (self.NUM_DIGITS, 15)


class TestClassifier(util.MNIST):
    def _build(self, *hiddens, **kwargs):
        return theanets.Classifier(
            layers=(self.DIGIT_SIZE, ) + hiddens + (10, ),
            hidden_activation='logistic',
            **kwargs)

    def test_classify_onelayer(self):
        net = self._build(13)
        z = net.classify(self.images)
        assert z.shape == (self.NUM_DIGITS, )

    def test_classify_twolayer(self):
        net = self._build(13, 14)
        z = net.classify(self.images)
        assert z.shape == (self.NUM_DIGITS, )


class TestAutoencoder(util.MNIST):
    def _build(self, *hiddens, **kwargs):
        return theanets.Autoencoder(
            layers=(self.DIGIT_SIZE, ) + hiddens + (self.DIGIT_SIZE, ),
            hidden_activation='logistic',
            **kwargs)

    def test_encode_onelayer(self):
        net = self._build(13)
        z = net.encode(self.images)
        assert z.shape == (self.NUM_DIGITS, 13)

    def test_encode_twolayer(self):
        net = self._build(13, 14)
        z = net.encode(self.images)
        assert z.shape == (self.NUM_DIGITS, 14)

    def test_encode_threelayer(self):
        net = self._build(13, 14, 15)
        z = net.encode(self.images)
        assert z.shape == (self.NUM_DIGITS, 14)

    def test_decode_onelayer(self):
        net = self._build(13)
        x = net.decode(net.encode(self.images))
        assert x.shape == (self.NUM_DIGITS, self.DIGIT_SIZE)

    def test_decode_twolayer(self):
        net = self._build(13, 14)
        x = net.decode(net.encode(self.images))
        assert x.shape == (self.NUM_DIGITS, self.DIGIT_SIZE)

    def test_decode_threelayer(self):
        net = self._build(13, 14, 15)
        x = net.decode(net.encode(self.images))
        assert x.shape == (self.NUM_DIGITS, self.DIGIT_SIZE)
        #err = ((valid - xhat) ** 2).sum(axis=1).mean()
